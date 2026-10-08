"""说话人分离（Speaker Diarization）—— 方案 A：段级对齐。

用 diarize（FoxNoseTech，Apache 2.0，纯 CPU）识别「谁在什么时候说话」，
再把 ASR 分段按说话人切换点切分，实现「不同人说话自动换行」。

设计：尽力而为。diarization 失败（无语音 / 模型异常）时返回空，不阻塞转录。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..subtitle.models import Segment, Word

log = logging.getLogger(__name__)


@dataclass
class SpeakerTurn:
    """一个说话人的一段连续语音。"""

    start: float
    end: float
    speaker: str


@dataclass
class DiarizationResult:
    turns: list[SpeakerTurn] = field(default_factory=list)
    num_speakers: int = 0

    @property
    def ok(self) -> bool:
        return self.num_speakers >= 1


def run_diarization(audio_path: str) -> DiarizationResult:
    """对音频做说话人分离。失败返回空结果（不抛异常）。"""
    try:
        from diarize import diarize

        r = diarize(audio_path)
        turns = [
            SpeakerTurn(start=float(s.start), end=float(s.end), speaker=str(s.speaker))
            for s in r.segments
        ]
        result = DiarizationResult(turns=turns, num_speakers=int(r.num_speakers))
        log.info(
            "说话人分离：检测到 %d 个说话人，%d 个时间段", result.num_speakers, len(turns)
        )
        return result
    except Exception as exc:  # noqa: BLE001
        log.warning("说话人分离失败，跳过（不影响转录）: %s", exc)
        return DiarizationResult()


def speaker_boundaries(turns: list[SpeakerTurn]) -> list[float]:
    """返回说话人切换时间点（秒），按时间排序去重。"""
    bounds: list[float] = []
    prev: str | None = None
    for t in sorted(turns, key=lambda x: x.start):
        if prev is not None and t.speaker != prev:
            bounds.append(t.start)
        prev = t.speaker
    return sorted(set(round(b, 2) for b in bounds))


def split_segments_by_speakers(
    segments: list[Segment], boundaries: list[float]
) -> list[Segment]:
    """按说话人切换点切分 ASR 分段（段级，用词级时间戳分配文本）。

    无词级时间戳的分段整段保留，避免按比例截断导致文本错乱。
    """
    if not boundaries:
        return segments

    out: list[Segment] = []
    for seg in segments:
        cuts = [b for b in boundaries if seg.start + 0.05 < b < seg.end - 0.05]
        if not cuts:
            out.append(seg)
            continue

        words = seg.words or []
        if len(words) < 2:
            # 没有足够的词级时间戳，整段保留
            out.append(seg)
            continue

        points = [seg.start] + sorted(cuts) + [seg.end]
        for i in range(len(points) - 1):
            a, b = points[i], points[i + 1]
            sub = [w for w in words if a <= w.start < b]
            if not sub:
                continue
            text = _join_words([w.text for w in sub])
            if not text:
                continue
            out.append(
                Segment(
                    start=sub[0].start,
                    end=sub[-1].end,
                    text=text,
                    words=sub,
                    avg_logprob=seg.avg_logprob,
                    no_speech_prob=seg.no_speech_prob,
                    source=seg.source,
                )
            )
    return out


def _join_words(pieces: list[str]) -> str:
    """拼接词：CJK 之间不加空格，拉丁词之间加空格。"""
    out = ""
    for piece in pieces:
        p = piece.strip()
        if not p:
            continue
        if not out:
            out = p
            continue
        prev = out[-1]
        if _is_cjk(prev) or _is_cjk(p[0]) or prev in "，。！？、；：!?,.:;":
            out += p
        elif p[0] in "，。！？、；：!?,.:;）)】」』":
            out += p
        else:
            out += " " + p
    return out


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (
        0x3400 <= o <= 0x4DBF
        or 0x4E00 <= o <= 0x9FFF
        or 0x3040 <= o <= 0x30FF
        or 0xAC00 <= o <= 0xD7AF
    )
