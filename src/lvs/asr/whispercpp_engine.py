"""whisper.cpp (GGML) 引擎 —— CPU/无 CUDA 环境下的降级路径。

依赖 pywhispercpp（可选装）：uv pip install pywhispercpp
"""
from __future__ import annotations

import time
import wave
from pathlib import Path

from ..config import Config
from ..subtitle.models import Segment
from .base import AsrEngine, AsrError, AsrOptions, AsrResult, ProgressFn


class WhisperCppEngine(AsrEngine):
    name = "whispercpp"

    def __init__(
        self,
        cfg: Config,
        options: AsrOptions,
        model_path: str,
        path_source: str = "config",
    ) -> None:
        super().__init__(cfg, options)
        self.model_path = model_path
        self.path_source = path_source
        self._model = None
        self._backend = ""

    # ------------------------------------------------------------------
    def _load(self, progress: ProgressFn | None = None) -> None:
        if self._model is not None:
            return
        try:
            from pywhispercpp.model import Model  # type: ignore
        except ImportError as exc:
            raise AsrError(
                "未安装 pywhispercpp。请执行: uv pip install pywhispercpp\n"
                "（或改用 faster-whisper 引擎）"
            ) from exc

        mp = Path(self.model_path)
        if not mp.exists():
            raise AsrError(
                f"whisper.cpp 模型不存在: {self.model_path}\n"
                "请下载 ggml-*.bin 放到 models/asr/ 目录下（见 scripts/download_models.py）"
            )

        if progress:
            progress(0.0, f"加载 whisper.cpp 模型 {mp.name}")
        try:
            self._model = Model(
                str(mp),
                n_threads=self.options.cpu_threads or 0,
                print_progress=False,
                print_realtime=False,
            )
        except Exception as exc:  # noqa: BLE001
            raise AsrError(f"加载 whisper.cpp 模型失败: {exc}") from exc
        self._backend = "pywhispercpp"
        if progress:
            progress(0.0, f"模型就绪（{self._backend}）")

    # ------------------------------------------------------------------
    def transcribe(
        self,
        audio_path: str | Path,
        progress: ProgressFn | None = None,
    ) -> AsrResult:
        self._load(progress)
        assert self._model is not None

        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise AsrError(f"音频文件不存在: {audio_path}")

        duration = _wav_duration(audio_path)
        opts = self.options
        if progress:
            progress(0.05, f"whisper.cpp 转录中（{duration:.0f}s 音频）")

        kwargs: dict = {}
        if opts.language:
            kwargs["language"] = opts.language
        if opts.initial_prompt:
            kwargs["initial_prompt"] = opts.initial_prompt
        kwargs["translate"] = opts.task == "translate"

        t0 = time.time()
        try:
            raw = self._model.transcribe(str(audio_path), **kwargs)
        except TypeError:
            kwargs.pop("translate", None)
            raw = self._model.transcribe(str(audio_path), **kwargs)
        except Exception as exc:  # noqa: BLE001
            raise AsrError(f"whisper.cpp 转录失败: {exc}") from exc

        segments: list[Segment] = []
        for item in raw:
            # pywhispercpp 的新版返回对象，旧版返回 (t0, t1, text) 元组
            if isinstance(item, (tuple, list)):
                t0_, t1_, text = (list(item) + ["", "", ""])[:3]
            else:
                t0_ = float(getattr(item, "t0", 0.0)) / 100.0
                t1_ = float(getattr(item, "t1", 0.0)) / 100.0
                text = getattr(item, "text", "")
            text = (text or "").strip()
            if not text:
                continue
            segments.append(
                Segment(
                    start=_normalize_ts(t0_, duration),
                    end=_normalize_ts(t1_, duration),
                    text=text,
                    words=[],  # whisper.cpp 默认不给词级时间戳
                    source="asr",
                )
            )

        if progress:
            progress(0.99, f"转录完成 · {len(segments)} 段 · 耗时 {time.time() - t0:.0f}s")

        return AsrResult(
            segments=segments,
            language=opts.language or "auto",
            duration=duration,
            engine=self.name,
        )

    def close(self) -> None:
        self._model = None

    def describe(self) -> str:
        name = Path(self.model_path).name if Path(self.model_path).exists() else self.model_path
        return f"whisper.cpp · {name} · 来源:{self.path_source}"


# --------------------------------------------------------------------------
def _normalize_ts(value: float, duration: float) -> float:
    """whisper.cpp 有时给毫秒，有时给百分秒，统一到秒。"""
    v = float(value)
    if v <= 0:
        return 0.0
    if v > duration * 1.5 + 10:      # 明显偏大 -> 毫秒
        v /= 1000.0
    if v > duration * 1.5 + 10:      # 还是偏大 -> 百分秒
        v /= 100.0
    return max(0.0, min(v, duration if duration > 0 else v))


def _wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate() or 1
            return frames / rate
    except Exception:  # noqa: BLE001
        return 0.0
