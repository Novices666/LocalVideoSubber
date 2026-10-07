"""全局工作区状态。

前端六个页面是独立的，但转录 → 翻译 → 渲染的数据需要跨页流转。
工作区在内存中持有「当前正在处理的素材」，供各页自动带入。

    - 转录完成  → 工作区持有字幕 cues
    - 翻译完成  → 工作区更新 cues 的译文
    - 渲染完成  → 工作区记录最近产物

同时允许手动切换：从 output/ 目录加载某个 srt 覆盖当前字幕。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .jobs import Job, JobStatus
from .subtitle.io import load_subtitle
from .subtitle.models import Cue, stats


@dataclass
class Workspace:
    """线程安全的工作区状态。"""

    video: str = ""
    video_stem: str = ""
    cues: list[Cue] = field(default_factory=list)
    last_output: str = ""
    work_dir: str = ""
    updated_at: float = 0.0
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    # ------------------------------------------------------------------
    def set_video(self, path: str) -> None:
        with self._lock:
            self.video = path
            if path:
                self.video_stem = Path(path).stem
            self.updated_at = time.time()

    def set_cues(self, cues: list[Cue], video: str = "") -> None:
        with self._lock:
            self.cues = list(cues)
            if video:
                self.video = video
                self.video_stem = Path(video).stem
            self.updated_at = time.time()

    def merge_translations(self, cues: list[Cue]) -> None:
        """翻译完成后，把译文合并进当前字幕（按 index 对齐）。"""
        with self._lock:
            if not self.cues:
                self.cues = list(cues)
                self.updated_at = time.time()
                return
            by_index = {c.index: c for c in self.cues}
            for c in cues:
                target = by_index.get(c.index)
                if target is not None:
                    target.translation = c.translation
                    target.text = c.text
            self.updated_at = time.time()

    def set_output(self, path: str) -> None:
        with self._lock:
            self.last_output = path
            self.updated_at = time.time()

    def set_work_dir(self, path: str) -> None:
        with self._lock:
            self.work_dir = path
            self.updated_at = time.time()

    def load_cues_from_file(self, path: str, as_translation: bool = False) -> list[Cue]:
        """手动从字幕文件加载，覆盖当前字幕。"""
        cues = load_subtitle(path, as_translation=as_translation)
        with self._lock:
            self.cues = cues
            self.updated_at = time.time()
        return cues

    def clear(self) -> None:
        with self._lock:
            self.video = ""
            self.video_stem = ""
            self.cues = []
            self.last_output = ""
            self.updated_at = time.time()

    # ------------------------------------------------------------------
    def update_from_job(self, job: Job) -> None:
        """任务完成时，把结果吸入工作区。由 JobQueue 的 on_update 回调触发。"""
        if job.status != JobStatus.DONE:
            return
        result = job.result or {}

        if job.kind == "transcribe":
            cues = [Cue.from_dict(d) for d in result.get("cues", [])]
            if cues:
                video = result.get("source", self.video)
                self.set_cues(cues, video)
            if result.get("work_dir"):
                self.set_work_dir(result["work_dir"])
            if result.get("srt"):
                self.last_output = result["srt"]

        elif job.kind == "translate":
            cues = [Cue.from_dict(d) for d in result.get("cues", [])]
            if cues:
                self.merge_translations(cues)
            if result.get("bilingual_srt"):
                self.last_output = result["bilingual_srt"]
            if result.get("work_dir"):
                self.set_work_dir(result["work_dir"])

        elif job.kind == "render":
            if result.get("output"):
                self.set_output(result["output"])
            if result.get("files"):
                self.set_output(result["files"][0])

        elif job.kind == "resegment":
            cues = [Cue.from_dict(d) for d in result.get("cues", [])]
            if cues:
                self.set_cues(cues, self.video)

    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            translated = sum(1 for c in self.cues if c.translation)
            return {
                "video": self.video,
                "video_stem": self.video_stem,
                "cue_count": len(self.cues),
                "translated": translated,
                "last_output": self.last_output,
                "work_dir": self.work_dir,
                "updated_at": self.updated_at,
                "stats": stats(self.cues) if self.cues else None,
                "cues": [c.to_dict() for c in self.cues],
            }

    def cues_dicts(self) -> list[dict[str, Any]]:
        with self._lock:
            return [c.to_dict() for c in self.cues]


# --------------------------------------------------------------------------
# 全局单例
# --------------------------------------------------------------------------
_workspace: Workspace | None = None
_ws_lock = threading.Lock()


def get_workspace() -> Workspace:
    global _workspace
    with _ws_lock:
        if _workspace is None:
            _workspace = Workspace()
    return _workspace
