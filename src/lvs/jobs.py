"""任务队列：单任务串行 + 取消令牌 + 进度事件。

设计：
    - 单个 worker 线程串行消费（8G 显存不能同时跑 ASR 和 LLM）
    - 每个 Job 持有 CancelToken，各阶段循环里检查
    - 进度事件写入 Job 内部状态，供调用方读取
"""
from __future__ import annotations

import logging
import queue
import threading
import time
import traceback
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)


class JobStatus(str, Enum):
    QUEUED = "排队中"
    RUNNING = "运行中"
    DONE = "已完成"
    FAILED = "失败"
    CANCELLED = "已取消"

    @property
    def tag(self) -> str:
        """文本状态标签（不使用 emoji）。"""
        return {
            "排队中": "排队", "运行中": "运行", "已完成": "完成",
            "失败": "失败", "已取消": "取消",
        }[self.value]

    @property
    def finished(self) -> bool:
        return self in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED)


# --------------------------------------------------------------------------
class TaskCancelled(Exception):
    """任务被取消。"""


class CancelToken:
    """取消令牌。传给各阶段的 cancel 参数。"""

    def __init__(self) -> None:
        self._ev = threading.Event()

    def is_set(self) -> bool:
        return self._ev.is_set()

    def set(self) -> None:
        self._ev.set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._ev.wait(timeout)

    def raise_if_cancelled(self) -> None:
        if self._ev.is_set():
            raise TaskCancelled()


# --------------------------------------------------------------------------
@dataclass
class Job:
    """一个处理任务。"""

    id: str
    kind: str                                    # transcribe | translate | render | full
    title: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    ended_at: float = 0.0

    status: JobStatus = JobStatus.QUEUED
    stage: str = ""                              # 当前阶段名
    progress: float = 0.0                        # 0..1
    message: str = ""
    logs: list[str] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    cancel_token: CancelToken = field(default_factory=CancelToken)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _log_seq: int = 0

    # -- 线程安全写入 --------------------------------------------------
    def update(self, progress: float | None = None, message: str | None = None,
               stage: str | None = None) -> None:
        with self._lock:
            if progress is not None:
                self.progress = max(0.0, min(1.0, float(progress)))
            if message is not None:
                self.message = message
            if stage is not None:
                self.stage = stage

    def log(self, text: str, level: str = "info") -> None:
        stamp = time.strftime("%H:%M:%S")
        prefix = {"info": "", "warn": "[注意] ", "error": "[错误] ", "ok": "[完成] "}.get(level, "")
        with self._lock:
            self._log_seq += 1
            if self._log_seq > 2000:          # 防内存膨胀
                self.logs = self.logs[-1000:]
                self._log_seq = len(self.logs)
            self.logs.append(f"[{stamp}] {prefix}{text}")
            self.message = text

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "id": self.id,
                "kind": self.kind,
                "title": self.title,
                "status": self.status.value,
                "status_tag": self.status.tag,
                "stage": self.stage,
                "progress": self.progress,
                "message": self.message,
                "logs": list(self.logs),
                "result": dict(self.result),
                "error": self.error,
                "elapsed": round(
                    (self.ended_at or time.time()) - (self.started_at or time.time()), 1
                ) if self.started_at else 0.0,
            }

    @property
    def elapsed(self) -> float:
        if not self.started_at:
            return 0.0
        return (self.ended_at or time.time()) - self.started_at

    def request_cancel(self) -> None:
        self.cancel_token.set()
        with self._lock:
            if self.status in (JobStatus.QUEUED, JobStatus.RUNNING):
                self.log("收到取消请求…", "warn")


# --------------------------------------------------------------------------
JobRunner = Callable[[Job, "JobContext"], dict[str, Any]]


class JobContext:
    """传给 runner 的上下文，封装进度/日志/取消。"""

    def __init__(self, job: Job) -> None:
        self.job = job
        self.cancel = job.cancel_token

    def progress(self, value: float, message: str = "") -> None:
        self.job.update(progress=value, message=message or None)
        self.cancel.raise_if_cancelled()

    def stage(self, name: str, progress: float = 0.0, message: str = "") -> None:
        self.job.update(stage=name, progress=progress, message=message or None)

    def log(self, text: str, level: str = "info") -> None:
        self.job.log(text, level)

    def scaled(self, lo: float, hi: float) -> Callable[[float, str], None]:
        """把子阶段的 0..1 进度映射到 [lo, hi] 区间。"""

        def fn(p: float, msg: str = "") -> None:
            self.progress(lo + (hi - lo) * max(0.0, min(1.0, p)), msg)

        return fn

    def check(self) -> None:
        self.cancel.raise_if_cancelled()


class JobQueue:
    """单 worker 串行任务队列。"""

    def __init__(self, on_update: Callable[[Job], None] | None = None) -> None:
        self._q: queue.Queue[str] = queue.Queue()
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.RLock()
        self._runners: dict[str, JobRunner] = {}
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._current: str | None = None
        self._seq = 0
        self.on_update = on_update

    # ------------------------------------------------------------------
    def register(self, kind: str, runner: JobRunner) -> None:
        self._runners[kind] = runner

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._loop, name="lvs-worker", daemon=True)
        self._worker.start()

    def stop(self, wait: bool = False, timeout: float = 5.0) -> None:
        self._stop.set()
        with self._lock:
            for job in self._jobs.values():
                if not job.status.finished:
                    job.request_cancel()
        if wait and self._worker:
            self._worker.join(timeout=timeout)

    # ------------------------------------------------------------------
    def submit(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        title: str = "",
    ) -> Job:
        with self._lock:
            self._seq += 1
            jid = f"J{self._seq:04d}"
            job = Job(id=jid, kind=kind, title=title or kind, payload=payload or {})
            self._jobs[jid] = job
            self._order.append(jid)
        job.log(f"已加入队列：{job.title}")
        self._q.put(jid)
        self._notify(job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self, limit: int = 50) -> list[Job]:
        with self._lock:
            ids = self._order[-limit:]
            return [self._jobs[i] for i in reversed(ids) if i in self._jobs]

    def current(self) -> Job | None:
        with self._lock:
            return self._jobs.get(self._current) if self._current else None

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None or job.status.finished:
            return False
        job.request_cancel()
        return True

    def cancel_all(self) -> int:
        n = 0
        for job in self.list_jobs(limit=1000):
            if not job.status.finished and self.cancel(job.id):
                n += 1
        return n

    def clear_finished(self) -> int:
        with self._lock:
            keep = []
            removed = 0
            for jid in self._order:
                job = self._jobs.get(jid)
                if job and job.status.finished:
                    del self._jobs[jid]
                    removed += 1
                else:
                    keep.append(jid)
            self._order = keep
        return removed

    def pending_count(self) -> int:
        return sum(1 for j in self.list_jobs(1000) if j.status == JobStatus.QUEUED)

    # ------------------------------------------------------------------
    def _notify(self, job: Job) -> None:
        if self.on_update:
            try:
                self.on_update(job)
            except Exception:  # noqa: BLE001
                log.debug("on_update 回调异常", exc_info=True)

    def _loop(self) -> None:
        log.info("任务队列 worker 启动")
        while not self._stop.is_set():
            try:
                jid = self._q.get(timeout=0.5)
            except queue.Empty:
                continue

            job = self.get(jid)
            if job is None:
                continue
            if job.cancel_token.is_set():
                job.status = JobStatus.CANCELLED
                job.ended_at = time.time()
                job.log("排队期间被取消", "warn")
                self._notify(job)
                continue

            self._run(job)
        log.info("任务队列 worker 退出")

    def _run(self, job: Job) -> None:
        runner = self._runners.get(job.kind)
        with self._lock:
            self._current = job.id

        job.started_at = time.time()
        job.status = JobStatus.RUNNING
        job.log(f"开始执行：{job.title}")
        self._notify(job)

        ctx = JobContext(job)
        try:
            if runner is None:
                raise RuntimeError(f"没有注册 {job.kind} 类型的执行器")
            result = runner(job, ctx)
            job.result = result or {}
            if job.cancel_token.is_set():
                job.status = JobStatus.CANCELLED
                job.log("任务已取消", "warn")
            else:
                job.status = JobStatus.DONE
                job.progress = 1.0
                job.log(f"完成，耗时 {job.elapsed:.1f}s", "ok")
        except TaskCancelled:
            job.status = JobStatus.CANCELLED
            job.log("任务已取消", "warn")
        except Exception as exc:  # noqa: BLE001
            job.status = JobStatus.FAILED
            job.error = f"{type(exc).__name__}: {exc}"
            job.log(f"失败：{job.error}", "error")
            log.error("任务 %s 失败\n%s", job.id, traceback.format_exc())
        finally:
            job.ended_at = time.time()
            with self._lock:
                self._current = None
            self._notify(job)


# --------------------------------------------------------------------------
# 全局单例
# --------------------------------------------------------------------------
_queue_singleton: JobQueue | None = None
_singleton_lock = threading.Lock()


def get_queue() -> JobQueue:
    global _queue_singleton
    with _singleton_lock:
        if _queue_singleton is None:
            _queue_singleton = JobQueue()
    return _queue_singleton
