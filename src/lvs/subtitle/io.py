"""SRT / VTT / ASS 读写。"""
from __future__ import annotations

import re
from pathlib import Path

from .models import (
    Cue,
    fmt_ass_time,
    fmt_srt_time,
    fmt_vtt_time,
    parse_time,
    reindex,
)

# --------------------------------------------------------------------------
# SRT
# --------------------------------------------------------------------------
_SRT_BLOCK = re.compile(
    r"(?P<idx>\d+)\s*\n"
    r"(?P<start>[\d:.,]+)\s*-->\s*(?P<end>[\d:.,]+)[^\n]*\n"
    r"(?P<text>(?:(?!\n\s*\n).)*)",
    re.DOTALL,
)
_SRT_ARROW = re.compile(
    r"(?P<start>[\d:.,]+)\s*-->\s*(?P<end>[\d:.,]+)[^\n]*\n(?P<text>(?:(?!\n\s*\n).)*)",
    re.DOTALL,
)


def read_srt(path: str | Path, as_translation: bool = False) -> list[Cue]:
    """读取 SRT。as_translation=True 时把文本放进 translation 字段。"""
    raw = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    return parse_srt(raw, as_translation=as_translation)


def parse_srt(raw: str, as_translation: bool = False) -> list[Cue]:
    raw = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    cues: list[Cue] = []
    matches = list(_SRT_BLOCK.finditer(raw))
    if not matches:
        matches = list(_SRT_ARROW.finditer(raw))
    for m in matches:
        text = m.group("text").strip()
        if not text:
            continue
        cues.append(
            Cue(
                index=len(cues) + 1,
                start=parse_time(m.group("start")),
                end=parse_time(m.group("end")),
                text="" if as_translation else text,
                translation=text if as_translation else "",
            )
        )
    return _fix_overlaps(cues)


def write_srt(
    cues: list[Cue],
    path: str | Path,
    mode: str = "both",
    order: str = "target_top",
) -> Path:
    """mode: source | target | both"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    blocks = []
    for i, c in enumerate(cues, start=1):
        text = c.display_text(mode=mode, order=order)
        if not text.strip():
            continue
        blocks.append(
            f"{i}\n{fmt_srt_time(c.start)} --> {fmt_srt_time(c.end)}\n{text}\n"
        )
    p.write_text("\n".join(blocks), encoding="utf-8")
    return p


# --------------------------------------------------------------------------
# WebVTT
# --------------------------------------------------------------------------
_VTT_CUE = re.compile(
    r"(?:(?P<id>[^\n]+)\n)?"
    r"(?P<start>[\d:.]+)\s*-->\s*(?P<end>[\d:.]+)[^\n]*\n"
    r"(?P<text>(?:(?!\n\s*\n).)*)",
    re.DOTALL,
)


def read_vtt(path: str | Path) -> list[Cue]:
    raw = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    return parse_vtt(raw)


def parse_vtt(raw: str) -> list[Cue]:
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    raw = re.sub(r"^WEBVTT[^\n]*\n", "", raw)
    raw = re.sub(r"\n(?:NOTE|STYLE|REGION)[^\n]*\n(?:(?!\n\n).)*\n", "\n", raw, flags=re.DOTALL)
    cues: list[Cue] = []
    for m in _VTT_CUE.finditer(raw):
        text = re.sub(r"<[^>]+>", "", m.group("text")).strip()
        if not text:
            continue
        cues.append(
            Cue(
                index=len(cues) + 1,
                start=parse_time(m.group("start")),
                end=parse_time(m.group("end")),
                text=text,
            )
        )
    return _fix_overlaps(cues)


def write_vtt(cues: list[Cue], path: str | Path, mode: str = "both", order: str = "target_top") -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    out = ["WEBVTT", ""]
    for i, c in enumerate(cues, start=1):
        text = c.display_text(mode=mode, order=order)
        if not text.strip():
            continue
        out.append(str(i))
        out.append(f"{fmt_vtt_time(c.start)} --> {fmt_vtt_time(c.end)}")
        out.append(text)
        out.append("")
    p.write_text("\n".join(out), encoding="utf-8")
    return p


# --------------------------------------------------------------------------
# ASS / SSA
# --------------------------------------------------------------------------
_ASS_DIALOGUE = re.compile(
    r"^Dialogue:\s*(?P<layer>[^,]*),(?P<start>[^,]*),(?P<end>[^,]*),"
    r"(?P<style>[^,]*),(?P<name>[^,]*),[^,]*,[^,]*,[^,]*,[^,]*,(?P<text>.*)$",
    re.MULTILINE,
)


def read_ass(path: str | Path) -> list[Cue]:
    raw = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    return parse_ass(raw)


def parse_ass(raw: str) -> list[Cue]:
    cues: list[Cue] = []
    for m in _ASS_DIALOGUE.finditer(raw):
        text = m.group("text").strip()
        text = re.sub(r"\{[^}]*\}", "", text)          # 去 override tag
        text = text.replace("\\N", "\n").replace("\\n", "\n")
        if not text.strip():
            continue
        cues.append(
            Cue(
                index=len(cues) + 1,
                start=parse_time(m.group("start")),
                end=parse_time(m.group("end")),
                text=text,
                speaker=m.group("name").strip(),
                style=m.group("style").strip() or "Default",
            )
        )
    return _fix_overlaps(cues)


ASS_HEADER = """[Script Info]
; Generated by LocalVideoSubber
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},{primary},{secondary},{outline_c},{back_c},-1,0,0,0,100,100,0,0,1,{outline},{shadow},2,20,20,{margin_v},1
Style: Source,{font},{src_size},{primary},{secondary},{outline_c},{back_c},-1,0,0,0,100,100,0,0,1,{src_outline},{shadow},2,20,20,{src_margin_v},1
Style: Target,{font},{tgt_size},{primary},{secondary},{outline_c},{back_c},0,0,0,0,100,100,0,0,1,{tgt_outline},{shadow},2,20,20,{tgt_margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_ass_header(style: dict | None = None, bilingual: dict | None = None) -> str:
    """按配置生成 ASS 头部（含 Default / Source / Target 三个样式）。

    原文（Source）与译文（Target）的描边、边距可独立设置：
    - source_outline / target_outline 缺省时回退到 outline
    - source_margin_v / target_margin_v 缺省时用默认推算值
    """
    s = dict(style or {})
    b = dict(bilingual or {})
    src_size = int(b.get("source_font_size", 32))
    tgt_size = int(b.get("target_font_size", 42))
    font_size = int(s.get("font_size", 42))
    margin_v = int(s.get("margin_v", 40)) + max(0, tgt_size - font_size) // 2
    outline = int(s.get("outline", 2))

    src_outline = int(s.get("source_outline") or outline)
    tgt_outline = int(s.get("target_outline") or outline)
    _src_mv = s.get("source_margin_v")
    _tgt_mv = s.get("target_margin_v")
    src_margin_v = int(_src_mv) if _src_mv is not None else (margin_v + tgt_size + 8)
    tgt_margin_v = int(_tgt_mv) if _tgt_mv is not None else margin_v

    return ASS_HEADER.format(
        font=s.get("font_name", "Microsoft YaHei"),
        size=font_size,
        src_size=src_size,
        tgt_size=tgt_size,
        primary=s.get("primary_color", "&H00FFFFFF"),
        secondary=s.get("secondary_color", "&H000000FF"),
        outline_c=s.get("outline_color", "&H00000000"),
        back_c=s.get("back_color", "&H80000000"),
        outline=outline,
        shadow=int(s.get("shadow", 1)),
        margin_v=margin_v,
        src_outline=src_outline,
        tgt_outline=tgt_outline,
        src_margin_v=src_margin_v,
        tgt_margin_v=tgt_margin_v,
    )


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("\n", "\\N").replace("{", "(").replace("}", ")")


def write_ass(
    cues: list[Cue],
    path: str | Path,
    mode: str = "both",
    order: str = "target_top",
    style: dict | None = None,
    bilingual: dict | None = None,
) -> Path:
    """写 ASS。

    mode=source/target 时用 Default 样式单行；
    mode=both 时原文与译文各用独立样式定位，保证不互相挤压。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    out = [build_ass_header(style, bilingual)]

    for c in cues:
        start, end = fmt_ass_time(c.start), fmt_ass_time(c.end)
        layer = 0
        name = _ass_escape(c.speaker) if c.speaker else ""

        if mode == "both" and c.translation:
            src, tgt = _ass_escape(c.text), _ass_escape(c.translation)
            first, second = (tgt, src) if order == "target_top" else (src, tgt)
            first_style = "Target" if order == "target_top" else "Source"
            second_style = "Source" if order == "target_top" else "Target"
            out.append(
                f"Dialogue: {layer},{start},{end},{first_style},{name},0,0,0,,{first}"
            )
            out.append(
                f"Dialogue: {layer},{start},{end},{second_style},{name},0,0,0,,{second}"
            )
        else:
            text = c.display_text(mode=mode, order=order)
            if not text.strip():
                continue
            out.append(
                f"Dialogue: {layer},{start},{end},Default,{name},0,0,0,,{_ass_escape(text)}"
            )

    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    return p


# --------------------------------------------------------------------------
# 统一入口
# --------------------------------------------------------------------------
READERS = {".srt": read_srt, ".vtt": read_vtt, ".ass": read_ass, ".ssa": read_ass}
WRITERS = {".srt": write_srt, ".vtt": write_vtt, ".ass": write_ass}


def load_subtitle(path: str | Path, **kwargs) -> list[Cue]:
    """按扩展名自动选择解析器。"""
    p = Path(path)
    reader = READERS.get(p.suffix.lower())
    if reader is None:
        raise ValueError(f"不支持的格式: {p.suffix}（支持 .srt/.vtt/.ass/.ssa）")
    return reader(p, **kwargs)


def save_subtitle(cues: list[Cue], path: str | Path, **kwargs) -> Path:
    p = Path(path)
    writer = WRITERS.get(p.suffix.lower())
    if writer is None:
        raise ValueError(f"不支持的格式: {p.suffix}（支持 .srt/.vtt/.ass）")
    return writer(cues, p, **kwargs)


# --------------------------------------------------------------------------
# 辅助
# --------------------------------------------------------------------------
def _fix_overlaps(cues: list[Cue], min_duration: float = 0.05) -> list[Cue]:
    """修正零长度/重叠时间戳，保证严格递增不重叠。"""
    cues = [c for c in cues if c.text.strip()]
    cues.sort(key=lambda c: (c.start, c.end))
    for i, c in enumerate(cues):
        if c.end <= c.start:
            c.end = c.start + min_duration
        if i > 0:
            prev = cues[i - 1]
            if c.start < prev.end:
                # 重叠：把前一条尾巴收到本条起点
                if c.start - min_duration > prev.start:
                    prev.end = c.start
                else:
                    c.start = prev.end
                    if c.end <= c.start:
                        c.end = c.start + min_duration
    return reindex(cues)
