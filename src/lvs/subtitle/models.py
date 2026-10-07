"""字幕数据结构。

三层时间轴：
    Word    —— ASR 输出的词级时间戳
    Segment —— ASR 原始分段（粗粒度）
    Cue     —— 最终字幕条目（智能断句后的精细粒度）
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class Word:
    """词级时间戳。"""

    start: float
    end: float
    text: str
    probability: float = 1.0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class Segment:
    """ASR 原始分段。"""

    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)
    # ASR 置信度指标
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0
    source: str = "asr"  # asr | manual

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class Cue:
    """最终字幕条目（可含译文）。"""

    index: int
    start: float
    end: float
    text: str
    translation: str = ""
    speaker: str = ""
    style: str = "Default"

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    # ---- 双语输出 --------------------------------------------------------
    def display_text(self, mode: str = "both", order: str = "target_top") -> str:
        """mode: source | target | both"""
        if mode == "source" or not self.translation:
            return self.text
        if mode == "target":
            return self.translation
        if order == "target_top":
            return f"{self.translation}\n{self.text}"
        return f"{self.text}\n{self.translation}"

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Cue":
        return cls(
            index=int(d.get("index", 0)),
            start=float(d.get("start", 0.0)),
            end=float(d.get("end", 0.0)),
            text=str(d.get("text", "")),
            translation=str(d.get("translation", "") or ""),
            speaker=str(d.get("speaker", "") or ""),
            style=str(d.get("style", "Default") or "Default"),
        )


# --------------------------------------------------------------------------
# 时间工具
# --------------------------------------------------------------------------
def fmt_srt_time(seconds: float) -> str:
    """00:00:01,234"""
    if seconds < 0:
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def fmt_vtt_time(seconds: float) -> str:
    """00:00:01.234"""
    return fmt_srt_time(seconds).replace(",", ".")


def fmt_ass_time(seconds: float) -> str:
    """0:00:01.23 （ASS 用百分秒，单数字小时位）"""
    if seconds < 0:
        seconds = 0.0
    cs = int(round(seconds * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6_000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def parse_time(text: str) -> float:
    """解析 SRT/VTT/ASS 各种时间写法为秒。"""
    t = text.strip().replace(",", ".")
    parts = t.split(":")
    try:
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        if len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
        return float(parts[0])
    except (ValueError, IndexError):
        return 0.0


def reindex(cues: Iterable[Cue]) -> list[Cue]:
    """重排索引，保证从 1 连续。"""
    out = list(cues)
    for i, c in enumerate(out, start=1):
        c.index = i
    return out


def total_duration(cues: list[Cue]) -> float:
    return max((c.end for c in cues), default=0.0)


def stats(cues: list[Cue]) -> dict:
    """返回字幕统计信息。"""
    if not cues:
        return {"count": 0, "duration": 0.0, "chars": 0, "avg_chars": 0.0}
    chars = sum(len(c.text.replace("\n", "")) for c in cues)
    return {
        "count": len(cues),
        "duration": round(total_duration(cues), 2),
        "chars": chars,
        "avg_chars": round(chars / len(cues), 1),
        "translated": sum(1 for c in cues if c.translation),
    }
