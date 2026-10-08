"""智能断句：把 ASR 的粗粒度分段按字数/标点/时长重新切分成字幕条目。

不加这一层，whisper 常常吐出一整段两三行、塞满 40 个字的字幕。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Cue, Segment, Word, reindex

# 句末标点（强断）
_SENT_END = set("。！？!?…")
# 句中断点（中弱断）
_CLAUSE = set("，,、；;：:—－-")
# 拉丁句末
_SENT_END_LATIN = re.compile(r"[.!?]['\"\)\]]*\s")

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")


@dataclass
class SegmentOptions:
    enabled: bool = True
    max_chars_cjk: int = 18
    max_chars_latin: int = 42
    min_duration: float = 0.8
    max_duration: float = 8.0
    max_gap: float = 1.0
    max_lines: int = 2

    @classmethod
    def from_config(cls, cfg) -> "SegmentOptions":
        return cls(
            enabled=bool(cfg.get("segment.enabled", True)),
            max_chars_cjk=int(cfg.get("segment.max_chars_cjk", 18)),
            max_chars_latin=int(cfg.get("segment.max_chars_latin", 42)),
            min_duration=float(cfg.get("segment.min_duration", 0.8)),
            max_duration=float(cfg.get("segment.max_duration", 8.0)),
            max_gap=float(cfg.get("segment.max_gap", 1.0)),
            max_lines=int(cfg.get("segment.max_lines", 2)),
        )


def _is_cjk(ch: str) -> bool:
    return bool(_CJK.match(ch))


def _display_width(text: str) -> int:
    """CJK 字符记 2，其余记 1。"""
    return sum(2 if _is_cjk(c) else 1 for c in text)


def _max_width(opts: SegmentOptions) -> int:
    """按目标语言风格换算成 display width 上限。"""
    return max(opts.max_chars_cjk * 2, opts.max_chars_latin)


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------
def segments_to_cues(
    segments: list[Segment],
    opts: SegmentOptions | None = None,
) -> list[Cue]:
    """Segment 列表 -> 精细 Cue 列表。

    有词级时间戳时按词重切（时间轴最准）；
    没有时退化为整段按标点/字数切（时间按比例摊）。
    """
    opts = opts or SegmentOptions()
    if not opts.enabled:
        return reindex(
            [
                Cue(index=i, start=s.start, end=s.end, text=clean_text(s.text))
                for i, s in enumerate(segments, start=1)
                if s.text.strip()
            ]
        )

    cues: list[Cue] = []
    for seg in segments:
        if not seg.text.strip():
            continue
        if seg.words and len(seg.words) >= 2:
            cues.extend(_split_with_words(seg, opts))
        else:
            cues.extend(_split_by_text(seg, opts))

    cues = _enforce_timing(cues, opts)
    return reindex(cues)


def clean_text(text: str) -> str:
    """清掉 whisper 常见的冗余空白与残留标记。"""
    t = text.strip()
    t = re.sub(r"\s+", " ", t)
    # whisper 有时会把标记词吐出来
    t = re.sub(r"^[\[\(【（]\s*(?:音乐|music|掌声|applause|笑声|laughter|静音|silence|BLANK_AUDIO)\s*[\]\)】）]\s*$", "", t, flags=re.I)
    t = re.sub(r"\[(?:BLANK_AUDIO|MUSIC|APPLAUSE|LAUGHTER)\]", "", t, flags=re.I)
    return t.strip(" -\u2014")


# --------------------------------------------------------------------------
# 词级切分
# --------------------------------------------------------------------------
def _split_with_words(seg: Segment, opts: SegmentOptions) -> list[Cue]:
    limit = _max_width(opts)
    words = [w for w in seg.words if w.text.strip()]
    if not words:
        return _split_by_text(seg, opts)

    chunks: list[list[Word]] = []
    cur: list[Word] = []
    cur_w = 0

    for i, w in enumerate(words):
        wtxt = w.text.strip()
        wwidth = _display_width(wtxt)
        gap_before = (w.start - words[i - 1].end) if i > 0 else 0.0

        # 判断是否该在放进这个词之前断开
        if cur:
            would = cur_w + wwidth + 1
            ends_sentence = _ends_sentence(cur[-1].text)
            too_long = would > limit
            too_slow = gap_before >= opts.max_gap
            too_long_time = (cur[-1].end - cur[0].start) >= opts.max_duration
            # 只在句末标点 / 超长 / 停顿 / 超时处断，不在逗号处提前断，
            # 保证一句话完整（避免 "you know what you're in for today," 被逗号切开）
            if ends_sentence or too_long or too_slow or too_long_time:
                chunks.append(cur)
                cur, cur_w = [], 0

        cur.append(w)
        cur_w += wwidth

    if cur:
        chunks.append(cur)

    # 时长过短的相邻块合并
    chunks = _merge_short_chunks(chunks, opts)

    out = []
    for ch in chunks:
        text = _join_words([w.text for w in ch])
        if not text:
            continue
        out.append(
            Cue(index=0, start=ch[0].start, end=ch[-1].end, text=text)
        )
    return out


def _ends_sentence(text: str) -> bool:
    t = text.rstrip()
    if not t:
        return False
    if t[-1] in _SENT_END:
        return True
    # 英文句末：以 . ! ? 结尾（含引号/括号）即判为句末，不要求后面有空格。
    # whisper 词级时间戳里标点附着在词尾（"20."、"you?"），词本身无尾随空格。
    return bool(re.search(r"[.!?]['\"\)\]]*$", t))


def _ends_clause(text: str) -> bool:
    t = text.rstrip()
    return bool(t) and t[-1] in _CLAUSE


def _join_words(pieces: list[str]) -> str:
    """拼接词：CJK 之间不加空格，拉丁词之间加。"""
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
    return re.sub(r"\s+", " ", out).strip()


def _merge_short_chunks(chunks: list[list[Word]], opts: SegmentOptions) -> list[list[Word]]:
    """把短于 min_duration 的块并入邻居，避免字幕一闪而过。"""
    if len(chunks) < 2:
        return chunks
    limit = _max_width(opts)
    merged: list[list[Word]] = []
    for ch in chunks:
        if merged:
            prev = merged[-1]
            prev_dur = prev[-1].end - prev[0].start
            ch_dur = ch[-1].end - ch[0].start
            prev_w = sum(_display_width(w.text.strip()) for w in prev)
            ch_w = sum(_display_width(w.text.strip()) for w in ch)
            joinable = (
                (prev_dur < opts.min_duration or ch_dur < opts.min_duration)
                and prev_w + ch_w <= limit * 1.15
                and (ch[-1].end - prev[0].start) <= opts.max_duration * 1.3
            )
            if joinable and not _ends_sentence(prev[-1].text):
                merged[-1] = prev + ch
                continue
        merged.append(ch)
    return merged


# --------------------------------------------------------------------------
# 纯文本切分（无词级时间戳时的退化路径）
# --------------------------------------------------------------------------
def _split_by_text(seg: Segment, opts: SegmentOptions) -> list[Cue]:
    text = clean_text(seg.text)
    if not text:
        return []

    pieces = _split_text_pieces(text, _max_width(opts))
    if not pieces:
        return []

    # 按字符占比摊时间
    weights = [max(1, _display_width(p)) for p in pieces]
    total_w = sum(weights)
    span = max(0.01, seg.end - seg.start)

    cues: list[Cue] = []
    t = seg.start
    for p, w in zip(pieces, weights):
        share = span * (w / total_w)
        cues.append(Cue(index=0, start=t, end=min(seg.end, t + share), text=p))
        t += share
    if cues:
        cues[-1].end = seg.end
    return cues


def _split_text_pieces(text: str, limit: int) -> list[str]:
    """先按句末切，再按子句切，最后按长度硬切。"""
    sentences = _split_keep(text, _SENT_END)
    out: list[str] = []
    for s in sentences:
        if _display_width(s) <= limit:
            out.append(s)
            continue
        clauses = _split_keep(s, _CLAUSE)
        buf = ""
        for cl in clauses:
            if not buf:
                buf = cl
            elif _display_width(buf + cl) <= limit:
                buf += cl
            else:
                out.append(buf)
                buf = cl
        if buf:
            out.append(buf)

    # 仍超长的，硬切
    final: list[str] = []
    for piece in out:
        while _display_width(piece) > limit:
            cut = _find_cut(piece, limit)
            final.append(piece[:cut])
            piece = piece[cut:]
        if piece.strip():
            final.append(piece)

    return [p.strip() for p in final if p.strip()]


def _split_keep(text: str, marks: set[str]) -> list[str]:
    """按标点切分并保留标点。"""
    out, buf = [], ""
    for ch in text:
        buf += ch
        if ch in marks:
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return out


def _find_cut(text: str, limit: int) -> int:
    """在 limit 宽度内找一个尽量靠后、且不在词中间的切点。"""
    width = 0
    best = 0
    for i, ch in enumerate(text):
        width += 2 if _is_cjk(ch) else 1
        if width <= limit:
            best = i + 1
            if ch in _CLAUSE or ch == " ":
                best = i + 1
        else:
            break
    return max(1, best)


# --------------------------------------------------------------------------
# 时间约束
# --------------------------------------------------------------------------
def _enforce_timing(cues: list[Cue], opts: SegmentOptions) -> list[Cue]:
    """保证：不重叠、不短于 min_duration、不超 max_duration。"""
    cues = [c for c in cues if c.text.strip()]
    for i, c in enumerate(cues):
        # 防重叠：与前一条至少留 20ms 间隙
        if i > 0:
            prev = cues[i - 1]
            if c.start < prev.end + 0.02:
                c.start = min(prev.end + 0.02, max(c.start, prev.start + 0.02))
        if c.end <= c.start:
            c.end = c.start + opts.min_duration
        # 过短：向后延（不侵入下一条）
        if c.duration < opts.min_duration:
            want = c.start + opts.min_duration
            nxt_start = cues[i + 1].start - 0.02 if i + 1 < len(cues) else float("inf")
            c.end = max(c.end, min(want, nxt_start))
            if c.end <= c.start:
                c.end = c.start + 0.2
    return cues


# --------------------------------------------------------------------------
# 反向：合并相邻条目（导出纯文本 / 重新分段时用）
# --------------------------------------------------------------------------
