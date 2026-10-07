"""字幕渲染：导出 / 软封装 / 硬烧录。

三种模式：
    1. 导出字幕文件（srt/vtt/ass）
    2. 软字幕封装（不重编码，秒级）
    3. 硬字幕烧录（NVENC 硬件编码，回退 libx264）
"""
from __future__ import annotations

import logging
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..media import Cancelled, ffmpeg_exe, probe, run_cancellable
from .io import save_subtitle, write_ass
from .models import Cue

log = logging.getLogger(__name__)

ProgressFn = Callable[[float, str], None]


class RenderError(Exception):
    """渲染错误。"""


# --------------------------------------------------------------------------
@dataclass
class RenderOptions:
    mode: str = "burn"                 # export | soft | burn
    container: str = "mkv"             # 软封装容器
    encoder: str = "auto"              # auto | h264_nvenc | hevc_nvenc | libx264
    crf: int = 20
    preset: str = "p5"
    display_mode: str = "both"         # source | target | both
    order: str = "target_top"
    style: dict | None = None
    bilingual: dict | None = None
    subtitle_format: str = "ass"       # 临时字幕格式（软封装用）
    audio_copy: bool = True
    # 烧录时的额外滤镜参数
    extra_vf: str = ""

    @classmethod
    def from_config(cls, cfg, **overrides) -> "RenderOptions":
        opts = cls(
            container=str(cfg.get("render.soft_container", "mkv")),
            encoder=str(cfg.get("render.burn_encoder", "auto")),
            crf=int(cfg.get("render.burn_crf", 20)),
            preset=str(cfg.get("render.burn_preset", "p5")),
            style=cfg.section("render").get("style") or {},
            bilingual=cfg.section("render").get("bilingual") or {},
        )
        for k, v in overrides.items():
            if v is not None and hasattr(opts, k):
                setattr(opts, k, v)
        return opts


@dataclass
class RenderResult:
    output: Path
    mode: str
    size_mb: float = 0.0
    note: str = ""


# --------------------------------------------------------------------------
# 编码器探测
# --------------------------------------------------------------------------
_encoder_cache: dict[str, bool] = {}


def available_encoders(force: bool = False) -> set[str]:
    global _encoder_cache
    if _encoder_cache and not force:
        return {k for k, v in _encoder_cache.items() if v}
    try:
        out = subprocess.run(
            [ffmpeg_exe(), "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=20,
            encoding="utf-8", errors="replace",
        ).stdout
    except Exception:  # noqa: BLE001
        return set()
    found = set()
    for name in ("h264_nvenc", "hevc_nvenc", "libx264", "libx265", "h264_qsv", "h264_amf"):
        _encoder_cache[name] = name in out
        if name in out:
            found.add(name)
    _encoder_cache["_scanned"] = True
    return found


def pick_encoder(prefer: str = "auto") -> str:
    """选编码器。auto 时优先 NVENC。"""
    avail = available_encoders()
    prefer = (prefer or "auto").lower()
    if prefer != "auto":
        return prefer if prefer in avail else "libx264"
    # 优先 HEVC NVENC（同画质体积更小），其次 H264 NVENC
    for enc in ("hevc_nvenc", "h264_nvenc", "libx264"):
        if enc in avail:
            return enc
    return "libx264"


# --------------------------------------------------------------------------
# 导出字幕文件
# --------------------------------------------------------------------------
def export_subtitles(
    cues: list[Cue],
    out_dir: str | Path,
    stem: str,
    formats: list[str] | None = None,
    mode: str = "both",
    order: str = "target_top",
    style: dict | None = None,
    bilingual: dict | None = None,
) -> list[Path]:
    """导出字幕文件，可一次导出多个格式。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    formats = formats or ["srt", "ass"]
    written: list[Path] = []

    # 单语模式时，纯译文另存一份 _zh / _target
    variants: list[tuple[str, str]] = [(mode, "")]
    if mode == "both" and any(c.translation for c in cues):
        variants.append(("target", ".translated"))
        variants.append(("source", ".source"))

    for fmt in formats:
        fmt = fmt.lower().lstrip(".")
        for m, suffix in variants:
            if fmt == "ass":
                p = out_dir / f"{stem}{suffix}.ass"
                write_ass(cues, p, mode=m, order=order, style=style, bilingual=bilingual)
            else:
                p = out_dir / f"{stem}{suffix}.{fmt}"
                save_subtitle(cues, p, mode=m, order=order)
            written.append(p)
    return written


# --------------------------------------------------------------------------
# 软字幕封装
# --------------------------------------------------------------------------
def _make_soft_subtitle(
    cues: list[Cue],
    work_dir: Path,
    fmt: str,
    mode: str,
    order: str,
    style: dict | None,
    bilingual: dict | None,
) -> Path:
    """生成用于封装的字幕文件。mkv 优先用 ASS（保留样式）。"""
    if fmt == "ass":
        p = work_dir / "soft.ass"
        write_ass(cues, p, mode=mode, order=order, style=style, bilingual=bilingual)
        return p
    p = work_dir / f"soft.{fmt}"
    save_subtitle(cues, p, mode=mode, order=order)
    return p


def mux_subtitles(
    video: str | Path,
    cues: list[Cue],
    out_path: str | Path,
    options: RenderOptions | None = None,
    progress: ProgressFn | None = None,
    cancel=None,
    language: str = "chi",
    title: str = "中文",
) -> RenderResult:
    """软封装：把字幕作为独立轨道塞进容器，不重编码。"""
    opts = options or RenderOptions()
    video = Path(video)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    container = out_path.suffix.lower().lstrip(".")
    # mp4 只认 mov_text，且不支持 ASS 样式
    if container == "mp4":
        sub_fmt = "srt"
    else:
        sub_fmt = "ass" if opts.subtitle_format == "ass" else opts.subtitle_format

    if progress:
        progress(0.1, "生成字幕文件")

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        sub_file = _make_soft_subtitle(
            cues, work, sub_fmt, opts.display_mode, opts.order, opts.style, opts.bilingual
        )

        if progress:
            progress(0.3, f"封装字幕轨（{container}）")
        if cancel is not None and cancel.is_set():
            raise Cancelled()

        cmd = [
            ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-i", str(video),
            "-i", str(sub_file),
            "-map", "0", "-map", "1:0",
        ]
        if opts.audio_copy:
            cmd += ["-c:a", "copy"]
        cmd += ["-c:v", "copy"]
        if container == "mp4":
            cmd += ["-c:s", "mov_text"]
        else:
            cmd += ["-c:s", "ass" if sub_fmt == "ass" else sub_fmt]
        cmd += [
            f"-metadata:s:s:0", f"language={language}",
            f"-metadata:s:s:0", f"title={title}",
            str(out_path),
        ]

        r = run_cancellable(cmd, cancel=cancel, timeout=1800)
        if r.returncode != 0:
            err = r.stderr.strip()
            if container == "mp4" and "mov_text" in err:
                raise RenderError(
                    "MP4 容器对字幕轨道限制较多，建议改用 mkv。\n"
                    f"ffmpeg: {err[:200]}"
                )
            raise RenderError(f"封装失败: {err[:400]}")

    if progress:
        progress(1.0, "封装完成")
    size = out_path.stat().st_size / 1024 / 1024 if out_path.exists() else 0
    return RenderResult(output=out_path, mode="soft", size_mb=round(size, 1),
                        note="原画质未重编码，播放器可开关字幕轨")


# --------------------------------------------------------------------------
# 硬字幕烧录
# --------------------------------------------------------------------------
def burn_subtitles(
    video: str | Path,
    cues: list[Cue],
    out_path: str | Path,
    options: RenderOptions | None = None,
    progress: ProgressFn | None = None,
    cancel=None,
) -> RenderResult:
    """硬烧录：字幕压进画面。"""
    opts = options or RenderOptions()
    video = Path(video)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    info = probe(video)
    total = info.duration or 0.0

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        # 烧录一律用 ASS，才能控制双语样式
        sub_file = work / "burn.ass"
        write_ass(
            cues, sub_file,
            mode=opts.display_mode, order=opts.order,
            style=opts.style, bilingual=opts.bilingual,
        )

        encoder = pick_encoder(opts.encoder)
        if progress:
            progress(0.05, f"烧录中（{encoder}）")

        # ASS 滤镜路径需要转义（Windows 盘符冒号 + 反斜杠）
        sub_path = _escape_sub_path(sub_file)
        vf = f"subtitles='{sub_path}'"
        if opts.extra_vf:
            vf += "," + opts.extra_vf

        cmd = [
            ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-i", str(video),
            "-vf", vf,
            "-c:v", encoder,
        ]
        cmd += _encoder_args(encoder, opts.crf, opts.preset)
        cmd += ["-c:a", "copy"]
        cmd += ["-progress", "pipe:1", "-nostats"]
        cmd += [str(out_path)]

        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
        except FileNotFoundError as exc:
            raise RenderError("未找到 ffmpeg") from exc

        tail: list[str] = []
        started = time.monotonic()
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if cancel is not None and cancel.is_set():
                    proc.kill()
                    raise Cancelled()
                if time.monotonic() - started > 1800:
                    proc.kill()
                    raise RenderError("烧录超时（>1800s）")
                line = line.strip()
                if line.startswith("out_time_ms=") and total > 0 and progress:
                    try:
                        cur = int(line.split("=", 1)[1]) / 1_000_000
                        # 烧录通常比实时快，但进度按时间轴走
                        progress(min(0.98, cur / total), f"烧录 {cur:.0f}/{total:.0f}s")
                    except (ValueError, IndexError):
                        pass
            proc.wait(timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
            if proc.stderr is not None:
                tail.append(proc.stderr.read() or "")

        if proc.returncode != 0:
            err = "".join(tail).strip()
            # NVENC 常见失败：驱动/会话数限制，回退 CPU
            if "nvenc" in encoder and ("nvenc" in err.lower() or "no capable devices" in err.lower()):
                log.warning("NVENC 失败，回退 libx264: %s", err[:200])
                if progress:
                    progress(0.05, "NVENC 不可用，回退 libx264 重新烧录…")
                return burn_subtitles(
                    video, cues, out_path,
                    options=_with_encoder(opts, "libx264"),
                    progress=progress, cancel=cancel,
                )
            raise RenderError(f"烧录失败: {err[:400]}")

    if progress:
        progress(1.0, "烧录完成")
    size = out_path.stat().st_size / 1024 / 1024 if out_path.exists() else 0
    return RenderResult(output=out_path, mode="burn", size_mb=round(size, 1),
                        note=f"已用 {encoder} 硬烧录")


def _with_encoder(opts: RenderOptions, encoder: str) -> RenderOptions:
    import copy

    new = copy.copy(opts)
    new.encoder = encoder
    return new


def _encoder_args(encoder: str, crf: int, preset: str) -> list[str]:
    if encoder in ("h264_nvenc", "hevc_nvenc"):
        # NVENC 用 -cq 控制质量，preset 是 p1..p7
        p = preset if preset and preset.startswith("p") else "p5"
        return ["-preset", p, "-cq", str(crf), "-rc", "vbr", "-b:v", "0"]
    if encoder in ("libx264", "libx265"):
        p = preset if preset in (
            "ultrafast", "superfast", "veryfast", "faster", "fast",
            "medium", "slow", "slower", "veryslow",
        ) else "medium"
        return ["-preset", p, "-crf", str(crf)]
    if encoder == "h264_qsv":
        return ["-global_quality", str(crf), "-look_ahead", "1"]
    if encoder == "h264_amf":
        return ["-quality", "balanced", "-rc", "cqp", "-qp_i", str(crf), "-qp_p", str(crf)]
    return ["-crf", str(crf)]


def _escape_sub_path(path: Path) -> str:
    """转义 ffmpeg subtitles 滤镜里的路径。

    Windows: C:\\a\\b.ass -> C\\:/a/b.ass
    """
    s = str(path.resolve())
    s = s.replace("\\", "/")
    if len(s) > 1 and s[1] == ":":
        s = s[0] + "\\:" + s[2:]
    # 单引号是滤镜参数的定界符，需要转义
    s = s.replace("'", r"\'")
    return s


# --------------------------------------------------------------------------
# 统一入口
# --------------------------------------------------------------------------
def render(
    video: str | Path | None,
    cues: list[Cue],
    out_path: str | Path,
    options: RenderOptions | None = None,
    progress: ProgressFn | None = None,
    cancel=None,
) -> RenderResult:
    """按 options.mode 分发。"""
    opts = options or RenderOptions()
    out_path = Path(out_path)

    if opts.mode == "soft":
        if video is None:
            raise RenderError("软封装需要原始视频")
        return mux_subtitles(video, cues, out_path, opts, progress, cancel)
    if opts.mode == "burn":
        if video is None:
            raise RenderError("硬烧录需要原始视频")
        return burn_subtitles(video, cues, out_path, opts, progress, cancel)
    if opts.mode == "export":
        paths = export_subtitles(
            cues, out_path.parent, out_path.stem,
            formats=["srt", "vtt", "ass"],
            mode=opts.display_mode, order=opts.order,
            style=opts.style, bilingual=opts.bilingual,
        )
        if progress:
            progress(1.0, f"导出 {len(paths)} 个文件")
        return RenderResult(
            output=paths[0], mode="export",
            note=f"导出 {len(paths)} 个文件到 {out_path.parent}",
        )
    raise RenderError(f"未知渲染模式: {opts.mode}")


