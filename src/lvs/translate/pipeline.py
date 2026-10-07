"""字幕翻译流水线。

核心是「分批打包 + 上下文窗口 + 严格对齐」：
    一批 N 条 -> 送 LLM -> 按编号回填 -> 缺条则降半重试 -> 仍缺则逐条兜底
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Callable

from ..subtitle.models import Cue
from .llm_client import (
    LlmClient,
    LlmConfig,
    LlmError,
    LlmConnectionError,
    extract_json,
    run_concurrent,
)
from .prompts import build_retry_prompt, build_system_prompt, build_user_prompt, lang_name

log = logging.getLogger(__name__)

ProgressFn = Callable[[float, str], None]


class TranslateError(Exception):
    """翻译错误。"""


@dataclass
class TranslateOptions:
    source_lang: str = "auto"
    target_lang: str = "zh"
    batch_size: int = 25
    context_size: int = 3
    min_batch_size: int = 5
    concurrency: int = 1
    glossary: dict[str, str] = field(default_factory=dict)
    system_prompt: str = ""
    # 重试控制
    max_batch_retries: int = 3
    # 是否保留原文（如果译文为空）
    keep_original_on_fail: bool = True

    @classmethod
    def from_config(cls, cfg) -> "TranslateOptions":
        gl = cfg.get("translate.glossary") or {}
        if not isinstance(gl, dict):
            gl = {}
        return cls(
            source_lang=str(cfg.get("translate.source_lang", "auto")),
            target_lang=str(cfg.get("translate.target_lang", "zh")),
            batch_size=max(1, int(cfg.get("translate.batch_size", 25))),
            context_size=max(0, int(cfg.get("translate.context_size", 3))),
            min_batch_size=max(1, int(cfg.get("translate.min_batch_size", 5))),
            concurrency=max(1, int(cfg.get("translate.concurrency", 1))),
            glossary={str(k): str(v) for k, v in gl.items()},
            system_prompt=str(cfg.get("translate.system_prompt", "") or ""),
        )


@dataclass
class TranslateResult:
    translated: int = 0
    failed: list[int] = field(default_factory=list)   # 失败条目的 index
    batches: int = 0
    retries: int = 0

    @property
    def total(self) -> int:
        return self.translated + len(self.failed)


# --------------------------------------------------------------------------
class SubtitleTranslator:
    """把 Cue 列表的 text 翻译进 translation 字段。"""

    def __init__(
        self,
        client: LlmClient,
        options: TranslateOptions | None = None,
        llm_config: LlmConfig | None = None,
    ) -> None:
        self.client = client
        self.options = options or TranslateOptions()
        self.llm_config = llm_config or client.config
        self.result = TranslateResult()
        self._stats_lock = threading.Lock()

    # ------------------------------------------------------------------
    def translate_cues(
        self,
        cues: list[Cue],
        progress: ProgressFn | None = None,
        cancel=None,
    ) -> TranslateResult:
        """原地翻译，返回统计。"""
        opts = self.options
        todo = [c for c in cues if c.text.strip()]
        if not todo:
            return self.result

        system = build_system_prompt(
            opts.source_lang, opts.target_lang, opts.glossary, opts.system_prompt
        )

        batches = self._make_batches(todo)
        total_batches = len(batches)
        if progress:
            progress(
                0.0,
                f"翻译 {len(todo)} 条 · {total_batches} 批 · 每批 {opts.batch_size} 条 · "
                f"并发 {opts.concurrency}",
            )

        done_count = 0
        # 上下文依赖前一批的译文；启用上下文时强制按批串行，避免完成顺序改变语义。
        concurrency = opts.concurrency
        if concurrency > 1 and opts.context_size > 0:
            concurrency = 1
            log.info("已启用跨批上下文：翻译并发自动降为 1，保证上下文顺序")

        results: list[str] = [""] * total_batches
        context_map: dict[int, list[tuple[int, str, str]]] = {}

        def work(bi: int, batch: list[Cue]) -> str:
            ctx = self._build_context(batch, context_map, bi)
            return self._translate_batch(batch, system, ctx, progress, cancel)

        def on_done(bi: int, value, err: Exception | None) -> None:
            nonlocal done_count
            done_count += 1
            if err is not None:
                log.warning("批次 %d 失败: %s", bi, err)
                with self._stats_lock:
                    self.result.failed.extend(c.index for c in batches[bi])
                results[bi] = ""
            else:
                results[bi] = value or ""
            # 记录本批译文供后续批次作上下文
            context_map[bi] = [
                (c.index, c.text, c.translation) for c in batches[bi] if c.translation
            ]
            if progress:
                frac = done_count / max(1, total_batches)
                progress(
                    frac,
                    f"翻译进度 {done_count}/{total_batches} 批 · "
                    f"已完成 {self.result.translated}/{len(todo)} 条",
                )

        try:
            run_concurrent(work, batches, concurrency, cancel, on_done)
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "_Cancelled":
                raise
            raise TranslateError(f"翻译失败: {exc}") from exc

        if progress:
            progress(
                1.0,
                f"翻译完成 · 成功 {self.result.translated} 条"
                + (f" · 失败 {len(self.result.failed)} 条" if self.result.failed else ""),
            )
        return self.result

    # ------------------------------------------------------------------
    def _make_batches(self, cues: list[Cue]) -> list[list[Cue]]:
        """按标点边界切批，尽量不在句子中间断。"""
        bs = self.options.batch_size
        if bs <= 0 or len(cues) <= bs:
            return [cues] if cues else []

        batches: list[list[Cue]] = []
        cur: list[Cue] = []
        for c in cues:
            cur.append(c)
            if len(cur) >= bs and _ends_sentence(c.text):
                batches.append(cur)
                cur = []
            elif len(cur) >= bs * 1.4:      # 兜底：超太多就硬断
                batches.append(cur)
                cur = []
        if cur:
            batches.append(cur)
        return batches

    def _build_context(
        self,
        batch: list[Cue],
        context_map: dict[int, list[tuple[int, str, str]]],
        batch_index: int,
    ) -> list[tuple[int, str, str]] | None:
        """取本批之前的 N 条作为上下文。"""
        n = self.options.context_size
        if n <= 0 or batch_index == 0:
            return None
        # 优先用上一批已翻译的尾部
        for prev in range(batch_index - 1, -1, -1):
            if prev in context_map and context_map[prev]:
                return context_map[prev][-n:]
        return None

    # ------------------------------------------------------------------
    def _translate_batch(
        self,
        batch: list[Cue],
        system: str,
        context: list[tuple[int, str, str]] | None,
        progress: ProgressFn | None,
        cancel,
    ) -> str:
        """翻译一批。含降级重试。"""
        items = [(c.index, c.text) for c in batch]
        with self._stats_lock:
            self.result.batches += 1

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": build_user_prompt(items, context)},
        ]

        if cancel is not None and cancel.is_set():
            raise TranslateError("已取消")

        try:
            reply = self.client.chat(
                messages, temperature=self._batch_temperature(0), cancel=cancel
            )
        except LlmConnectionError:
            raise
        except LlmError as exc:
            log.warning("批次 %s 调用失败，逐条兜底: %s", items[0][0], exc)
            self._fallback_line_by_line(batch, system, cancel)
            return ""

        mapping = self._parse_mapping(reply)
        filled = self._apply(batch, mapping)

        # 缺条 -> 重试
        attempt = 0
        while filled < len(batch) and attempt < self.options.max_batch_retries:
            attempt += 1
            with self._stats_lock:
                self.result.retries += 1
            missing = [c.index for c in batch if not c.translation]
            if not missing:
                break

            # 策略1：整批重发（温度略升，换措辞）
            if attempt == 1:
                try:
                    reply2 = self.client.chat(
                        messages,
                        temperature=self._batch_temperature(attempt),
                        cancel=cancel,
                    )
                    filled = self._apply(batch, self._parse_mapping(reply2))
                    if filled >= len(batch):
                        break
                    missing = [c.index for c in batch if not c.translation]
                except LlmError as exc:
                    log.warning("整批重试失败: %s", exc)

            # 策略2：只补缺的
            if len(missing) >= self.options.min_batch_size:
                try:
                    sub_items = [(c.index, c.text) for c in batch if c.index in set(missing)]
                    msg = messages + [
                        {"role": "user", "content": build_retry_prompt(items, missing)}
                    ]
                    reply3 = self.client.chat(
                        msg,
                        temperature=self._batch_temperature(attempt),
                        cancel=cancel,
                    )
                    filled = self._apply(batch, self._parse_mapping(reply3), only=set(missing))
                    if not [c for c in batch if not c.translation]:
                        break
                except LlmError as exc:
                    log.warning("补缺重试失败: %s", exc)

            # 策略3：拆半
            remaining = [c for c in batch if not c.translation]
            if len(remaining) >= self.options.min_batch_size * 2:
                mid = len(remaining) // 2
                for half in (remaining[:mid], remaining[mid:]):
                    try:
                        sub = [(c.index, c.text) for c in half]
                        m = [
                            {"role": "system", "content": system},
                            {"role": "user", "content": build_user_prompt(sub, context)},
                        ]
                        r = self.client.chat(
                            m,
                            temperature=self._batch_temperature(attempt),
                            cancel=cancel,
                        )
                        self._apply(batch, self._parse_mapping(r), only={c.index for c in half})
                    except LlmError as exc:
                        log.warning("拆半重试失败: %s", exc)
                if not [c for c in batch if not c.translation]:
                    break

        # 仍缺 -> 逐条
        still_missing = [c for c in batch if not c.translation]
        if still_missing:
            log.info("批次仍有 %d 条未译，逐条兜底", len(still_missing))
            self._fallback_line_by_line(still_missing, system, cancel)

        # 统计
        for c in batch:
            if c.translation:
                with self._stats_lock:
                    self.result.translated += 1
            else:
                with self._stats_lock:
                    self.result.failed.append(c.index)
                if self.options.keep_original_on_fail:
                    c.translation = c.text

        return ""

    def _batch_temperature(self, attempt: int) -> float:
        base = self.llm_config.temperature
        if attempt == 0:
            return base
        # 重试时提高一点温度，避免复读同样的错误输出
        return min(1.0, base + 0.15 * attempt)

    # ------------------------------------------------------------------
    def _fallback_line_by_line(self, cues: list[Cue], system: str, cancel) -> None:
        """逐条翻译兜底（最慢但最不容易串行）。"""
        for c in cues:
            if cancel is not None and cancel.is_set():
                raise TranslateError("已取消")
            if c.translation:
                continue
            try:
                reply = self.client.chat(
                    [
                        {"role": "system", "content": system},
                        {
                            "role": "user",
                            "content": (
                                f"把下面这一条字幕翻译成{lang_name(self.options.target_lang)}，"
                                "只输出译文本身，不要任何其它内容：\n" + c.text
                            ),
                        },
                    ],
                    temperature=0.2,
                    max_tokens=512,
                    cancel=cancel,
                )
                c.translation = _clean_reply(reply)
            except (LlmError, LlmConnectionError) as exc:
                log.warning("逐条兜底失败 index=%s: %s", c.index, exc)
                continue

    # ------------------------------------------------------------------
    @staticmethod
    def _parse_mapping(reply: str) -> dict[int, str]:
        """把 LLM 回复解析成 {编号: 译文}。"""
        try:
            data = extract_json(reply)
        except LlmError:
            # 尝试行格式：[1] 译文 / 1. 译文 / 1: 译文
            return _parse_line_format(reply)

        out: dict[int, str] = {}
        if isinstance(data, dict):
            # 可能是 {"1": "..."} 或 {"translations": [...]} 或 {"1": {"text": ...}}
            if "translations" in data and isinstance(data["translations"], (list, dict)):
                data = data["translations"]
            elif "result" in data and isinstance(data["result"], (list, dict)):
                data = data["result"]
            elif "subtitles" in data and isinstance(data["subtitles"], (list, dict)):
                data = data["subtitles"]

            if isinstance(data, dict):
                for k, v in data.items():
                    idx = _to_int(k)
                    if idx is None:
                        continue
                    if isinstance(v, dict):
                        v = v.get("translation") or v.get("text") or v.get("译文") or ""
                    out[idx] = _clean_reply(str(v))
            elif isinstance(data, list):
                for i, v in enumerate(data, start=1):
                    if isinstance(v, dict):
                        idx = _to_int(v.get("index") or v.get("id") or i) or i
                        txt = v.get("translation") or v.get("text") or v.get("译文") or ""
                    else:
                        idx, txt = i, str(v)
                    out[idx] = _clean_reply(str(txt))
        elif isinstance(data, list):
            for i, v in enumerate(data, start=1):
                if isinstance(v, dict):
                    idx = _to_int(v.get("index") or v.get("id") or i) or i
                    txt = v.get("translation") or v.get("text") or v.get("译文") or ""
                else:
                    idx, txt = i, str(v)
                out[idx] = _clean_reply(str(txt))

        if not out:
            return _parse_line_format(reply)
        return out

    @staticmethod
    def _apply(batch: list[Cue], mapping: dict[int, str], only: set[int] | None = None) -> int:
        """把译文写回（按编号对齐）。返回本批已填条数。"""
        by_index = {c.index: c for c in batch}
        for idx, text in mapping.items():
            if only is not None and idx not in only:
                continue
            cue = by_index.get(idx)
            if cue is None or cue.translation:
                continue
            if text.strip():
                cue.translation = text
        return sum(1 for c in batch if c.translation)


# --------------------------------------------------------------------------
def _to_int(value) -> int | None:
    try:
        return int(str(value).strip().lstrip("#[]()").strip())
    except (ValueError, AttributeError):
        return None


_LINE_PAT = None


def _parse_line_format(text: str) -> dict[int, str]:
    """解析 `1. 译文` / `[1] 译文` / `1: 译文` 这种朴素格式。"""
    import re

    global _LINE_PAT
    if _LINE_PAT is None:
        _LINE_PAT = re.compile(r"^\s*\[?(\d+)\]?\s*[\.\:、\)]\s*(.+)$")

    out: dict[int, str] = {}
    for line in text.splitlines():
        line = line.strip().strip("\"'")
        if not line:
            continue
        m = _LINE_PAT.match(line)
        if m:
            out[int(m.group(1))] = _clean_reply(m.group(2))
    return out


def _clean_reply(text: str) -> str:
    """清理译文噪声。"""
    t = (text or "").strip()
    # 去掉模型爱加的前后缀
    t = t.strip('"\'')
    t = t.strip()
    # 去掉 "译文：" 前缀
    import re

    t = re.sub(r"^(?:译文|翻译|Translation|Translated)\s*[:：]\s*", "", t, flags=re.I)
    # 合并多余空行
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _ends_sentence(text: str) -> bool:
    t = (text or "").rstrip()
    if not t:
        return False
    return t[-1] in "。！？!?…" or t.endswith(".")


# --------------------------------------------------------------------------
def translate_cues(
    cues: list[Cue],
    cfg=None,
    client: LlmClient | None = None,
    options: TranslateOptions | None = None,
    llm_config: LlmConfig | None = None,
    progress: ProgressFn | None = None,
    cancel=None,
) -> TranslateResult:
    """便捷入口：配置 -> 客户端 -> 翻译。"""
    if options is None:
        if cfg is None:
            raise TranslateError("需要 cfg 或 options 之一")
        options = TranslateOptions.from_config(cfg)
    if client is None:
        if llm_config is None:
            if cfg is None:
                raise TranslateError("需要 cfg 或 llm_config 之一")
            llm_config = LlmConfig.from_config(cfg)
        client = LlmClient(llm_config)

    translator = SubtitleTranslator(client, options, llm_config)
    return translator.translate_cues(cues, progress=progress, cancel=cancel)
