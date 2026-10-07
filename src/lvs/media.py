"""媒体处理：探测 / 抽音频 / 长视频分段。

全部基于 ffmpeg / ffprobe 子进程，支持进度回调与取消令牌。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ProgressFn = Callable[[float, str], None]  # (0..1, message)

VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".webm", ".m4v",
    ".mpg", ".mpeg", ".ts", ".m2ts", ".vob", ".rmvb", ".3gp", ".ogv", ".mts",
}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".amr"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS


class MediaError(Exception):
    """媒体处理错误。"""


class Cancelled(Exception):
    """任务被用户取消。"""


def run_cancellable(
    cmd: list[str],
    cancel=None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a quiet subprocess while allowing cooperative cancellation."""
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise MediaError(f"未找到可执行文件: {cmd[0]}") from exc

    started = time.monotonic()
    try:
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                proc.kill()
                proc.communicate()
                raise Cancelled()
            if timeout is not None and time.monotonic() - started > timeout:
                proc.kill()
                stdout, stderr = proc.communicate()
                detail = (stderr or stdout or "").strip()[:300]
                raise MediaError(f"子进程超时（>{timeout:.0f}s）{': ' + detail if detail else ''}")
            time.sleep(0.1)
        stdout, stderr = proc.communicate()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()

    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


# --------------------------------------------------------------------------
# 可执行文件定位
# --------------------------------------------------------------------------
def _which(name: str) -> str:
    exe = shutil.which(name)
    if exe:
        return exe
    # Windows 上 ffmpeg 可能没进 PATH，试着从 ffmpeg 同目录找 ffprobe
    other = "ffmpeg" if name == "ffprobe" else "ffprobe"
    o = shutil.which(other)
    if o:
        cand = Path(o).with_name(name + (".exe" if o.lower().endswith(".exe") else ""))
        if cand.exists():
            return str(cand)
    raise MediaError(f"未找到 {name}，请安装 FFmpeg 并加入 PATH")


def ffmpeg_exe() -> str:
    return _which("ffmpeg")


def ffprobe_exe() -> str:
    return _which("ffprobe")


# --------------------------------------------------------------------------
# 媒体探测
# --------------------------------------------------------------------------
@dataclass
class MediaInfo:
    path: Path
    duration: float = 0.0
    has_video: bool = False
    has_audio: bool = False
    width: int = 0
    height: int = 0
    fps: float = 0.0
    video_codec: str = ""
    audio_codec: str = ""
    sample_rate: int = 0
    channels: int = 0
    bit_rate: int = 0
    container: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}" if self.width else "-"

    @property
    def duration_text(self) -> str:
        return format_duration(self.duration)

    def summary(self) -> str:
        if not self.path.exists():
            return "文件不存在"
        parts = [
            f"**{self.path.name}**",
            f"时长 {self.duration_text}",
            f"容器 {self.container or '-'}",
        ]
        if self.has_video:
            parts.append(f"视频 {self.video_codec} {self.resolution} {self.fps:.2f}fps")
        if self.has_audio:
            parts.append(
                f"音频 {self.audio_codec} {self.sample_rate}Hz {self.channels}ch"
            )
        if not self.has_video and not self.has_audio:
            parts.append("[注意] 未检测到有效音视频流")
        return " · ".join(parts)


def probe(path: str | Path) -> MediaInfo:
    """ffprobe 探测媒体信息。"""
    p = Path(path)
    if not p.exists():
        raise MediaError(f"文件不存在: {p}")

    cmd = [
        ffprobe_exe(), "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(p),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as exc:
        raise MediaError("ffprobe 超时") from exc
    if r.returncode != 0:
        raise MediaError(f"ffprobe 失败: {r.stderr.strip()[:300]}")

    try:
        data = json.loads(r.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise MediaError(f"ffprobe 输出解析失败: {exc}") from exc

    info = MediaInfo(path=p, raw=data)
    fmt = data.get("format") or {}
    info.container = fmt.get("format_name", "")
    try:
        info.duration = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        info.duration = 0.0
    try:
        info.bit_rate = int(fmt.get("bit_rate") or 0)
    except (TypeError, ValueError):
        info.bit_rate = 0

    for st in data.get("streams", []):
        codec_type = st.get("codec_type")
        if codec_type == "video" and not info.has_video:
            # 排除封面图等附属流
            if st.get("disposition", {}).get("attached_pic"):
                continue
            info.has_video = True
            info.width = int(st.get("width") or 0)
            info.height = int(st.get("height") or 0)
            info.video_codec = st.get("codec_name", "")
            info.fps = _parse_fps(st.get("avg_frame_rate") or st.get("r_frame_rate"))
        elif codec_type == "audio" and not info.has_audio:
            info.has_audio = True
            info.audio_codec = st.get("codec_name", "")
            try:
                info.sample_rate = int(st.get("sample_rate") or 0)
            except (TypeError, ValueError):
                info.sample_rate = 0
            info.channels = int(st.get("channels") or 0)

    if info.duration <= 0:
        for st in data.get("streams", []):
            try:
                d = float(st.get("duration") or 0)
                if d > info.duration:
                    info.duration = d
            except (TypeError, ValueError):
                continue
    return info


def _parse_fps(value: str | None) -> float:
    if not value or value == "0/0":
        return 0.0
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else 0.0
        return float(value)
    except (ValueError, ZeroDivisionError):
        return 0.0


def format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "00:00"
    s = int(round(seconds))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# --------------------------------------------------------------------------
# 抽音频
# --------------------------------------------------------------------------
def extract_audio(
    src: str | Path,
    dst: str | Path,
    sample_rate: int = 16000,
    channels: int = 1,
    progress: ProgressFn | None = None,
    cancel: "CancelToken | None" = None,
    info: MediaInfo | None = None,
) -> Path:
    """抽成 16k 单声道 WAV（whisper 的标准输入）。"""
    src_p, dst_p = Path(src), Path(dst)
    dst_p.parent.mkdir(parents=True, exist_ok=True)

    if src_p.resolve() == dst_p.resolve():
        raise MediaError("源与目标相同")

    info = info or probe(src_p)
    total = info.duration or 0.0

    cmd = [
        ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-i", str(src_p),
        "-vn", "-map", "0:a:0?",
        "-ac", str(channels), "-ar", str(sample_rate),
        "-c:a", "pcm_s16le",
        "-progress", "pipe:1", "-nostats",
        str(dst_p),
    ]

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
    except FileNotFoundError as exc:
        raise MediaError("未找到 ffmpeg") from exc

    stderr_buf: list[str] = []
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if cancel is not None and cancel.is_set():
                proc.kill()
                raise Cancelled()
            line = line.strip()
            if line.startswith("out_time_ms=") and total > 0 and progress:
                try:
                    cur = int(line.split("=", 1)[1]) / 1_000_000
                    progress(min(0.99, cur / total), f"抽取音频 {cur:.0f}/{total:.0f}s")
                except (ValueError, IndexError):
                    pass
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
        if proc.stderr is not None:
            stderr_buf.append(proc.stderr.read() or "")

    if proc.returncode != 0:
        err = "".join(stderr_buf).strip()[:400]
        # 无音轨是常见情况，单独提示
        if not info.has_audio:
            raise MediaError("该文件没有音频流，无法转录")
        raise MediaError(f"抽取音频失败: {err}")

    if not dst_p.exists() or dst_p.stat().st_size < 1024:
        raise MediaError("抽取音频结果为空（可能没有音轨）")

    if progress:
        progress(1.0, "音频抽取完成")
    return dst_p


# --------------------------------------------------------------------------
# 取消令牌（避免与 jobs 循环导入）
# --------------------------------------------------------------------------
class CancelToken:
    """轻量取消信号。jobs.CancelToken 与之兼容（鸭子类型）。"""

    def __init__(self) -> None:
        import threading

        self._ev = threading.Event()

    def is_set(self) -> bool:
        return self._ev.is_set()

    def set(self) -> None:
        self._ev.set()

    def raise_if_cancelled(self) -> None:
        if self._ev.is_set():
            raise Cancelled()


# --------------------------------------------------------------------------
# 长视频分段
# --------------------------------------------------------------------------
@dataclass
class Chunk:
    index: int
    start: float
    end: float
    path: Path | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start


def plan_chunks(duration: float, threshold: float, chunk_len: float | None = None) -> list[Chunk]:
    """超过 threshold 就切成固定长度片段（默认 15 分钟），供逐段处理。"""
    if duration <= threshold or threshold <= 0:
        return [Chunk(index=0, start=0.0, end=max(0.0, duration))]
    step = chunk_len or 900.0
    chunks: list[Chunk] = []
    t = 0.0
    i = 0
    while t < duration:
        end = min(duration, t + step)
        chunks.append(Chunk(index=i, start=t, end=end))
        t = end
        i += 1
    return chunks


def slice_audio(
    src: str | Path,
    chunk: Chunk,
    out_dir: str | Path,
    sample_rate: int = 16000,
    cancel: "CancelToken | None" = None,
) -> Path:
    """从已抽好的 wav 里切出一段。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"chunk_{chunk.index:04d}.wav"
    cmd = [
        ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-ss", f"{chunk.start:.3f}", "-t", f"{chunk.duration:.3f}",
        "-i", str(src),
        "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le",
        str(dst),
    ]
    r = run_cancellable(cmd, cancel=cancel, timeout=max(120.0, chunk.duration * 4 + 60))
    if r.returncode != 0:
        raise MediaError(f"切分音频失败: {r.stderr.strip()[:300]}")
    chunk.path = dst
    return dst


# --------------------------------------------------------------------------
# 轨道探测（软封装前检查是否已有字幕轨）
# --------------------------------------------------------------------------
def list_subtitle_streams(path: str | Path) -> list[dict]:
    info = probe(path)
    out = []
    for st in info.raw.get("streams", []):
        if st.get("codec_type") == "subtitle":
            out.append({
                "index": st.get("index"),
                "codec": st.get("codec_name", ""),
                "language": (st.get("tags") or {}).get("language", ""),
                "title": (st.get("tags") or {}).get("title", ""),
            })
    return out


# --------------------------------------------------------------------------
# 文件扫描
# --------------------------------------------------------------------------
def list_media_files(folder: str | Path, recursive: bool = True) -> list[Path]:
    folder = Path(folder)
    if not folder.is_dir():
        return []
    it = folder.rglob("*") if recursive else folder.glob("*")
    return sorted(
        p for p in it
        if p.is_file() and p.suffix.lower() in MEDIA_EXTS
    )


def safe_stem(name: str) -> str:
    """文件名安全化：去掉非法字符，限制长度。"""
    stem = Path(name).stem
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" .")
    return stem[:120] or "untitled"
