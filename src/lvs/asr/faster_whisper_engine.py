"""faster-whisper (CTranslate2) 引擎。"""
from __future__ import annotations

import contextlib
import io as _io
import logging
import sys
import time
from pathlib import Path

from ..config import Config
from ..subtitle.models import Segment, Word
from .base import AsrEngine, AsrError, AsrOptions, AsrResult, ProgressFn, TranscriptionCancelled

log = logging.getLogger(__name__)

# CTranslate2 支持的计算精度
VALID_COMPUTE = {
    "default", "auto", "int8", "int8_float16", "int8_float32", "int8_bfloat16",
    "float16", "float32", "bfloat16",
}

_blackwell_cache: bool | None = None


def _gpu_is_blackwell() -> bool:
    """检测 GPU 是否为 Blackwell 架构（compute capability >= 12.0，即 RTX 50 系）。

    Blackwell 上 ctranslate2 禁用了 INT8，必须用 float16，否则报 cublas 错误。
    """
    global _blackwell_cache
    if _blackwell_cache is not None:
        return _blackwell_cache
    try:
        import subprocess

        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0 or not r.stdout.strip():
            _blackwell_cache = False
        else:
            cap = r.stdout.strip().splitlines()[0].strip()
            _blackwell_cache = float(cap) >= 12.0
    except Exception:  # noqa: BLE001
        _blackwell_cache = False
    return _blackwell_cache


def _wav_duration(path: Path) -> float:
    """读取 wav 文件时长（秒）。失败返回 0。"""
    try:
        import wave

        with wave.open(str(path), "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate() or 1
            return frames / rate
    except Exception:  # noqa: BLE001
        return 0.0


def _setup_cuda_dll_path() -> None:
    """确保 cuBLAS 已预加载（双保险）。

    主入口在 config.py 模块顶部（import ctranslate2 之前）已调用
    preload_cuda_dlls()。这里再调一次作双保险（幂等，已加载则命中），
    覆盖直接 import lvs.asr 而不经过 config 的场景。
    """
    from ..cuda_dll import preload_cuda_dlls

    preload_cuda_dlls()


class FasterWhisperEngine(AsrEngine):
    name = "faster-whisper"

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
        self.resolved_device = ""
        self.resolved_compute = ""

    # ------------------------------------------------------------------
    # 模型加载
    # ------------------------------------------------------------------
    def _load(self, progress: ProgressFn | None = None) -> None:
        if self._model is not None:
            return
        # 取消检查：模型加载前
        if self.options.cancel_token is not None and self.options.cancel_token.is_set():
            raise TranscriptionCancelled()
        # 关键：先加 nvidia DLL 目录，否则长音频特征提取会报 cublas 缺失
        _setup_cuda_dll_path()
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover
            raise AsrError(
                "未安装 faster-whisper，请执行: uv pip install faster-whisper"
            ) from exc

        # 让 HF 下载走可用镜像
        from ..config import setup_hf_mirror

        setup_hf_mirror(self.cfg)

        opts = self.options
        device, compute = self._resolve_device_compute()

        if progress:
            progress(0.0, f"加载模型 {Path(self.model_path).name or self.model_path} ({device}/{compute})")

        kwargs = dict(
            device=device,
            compute_type=compute,
            download_root=str(self.cfg.model_root / "asr"),
        )
        if opts.cpu_threads > 0:
            kwargs["cpu_threads"] = opts.cpu_threads
        if opts.num_workers > 1:
            kwargs["num_workers"] = opts.num_workers

        t0 = time.time()
        # 捕捉 CTranslate2 的 C 层日志噪声
        with _suppress_native_stderr():
            try:
                self._model = WhisperModel(self.model_path, **kwargs)
            except Exception as exc:  # noqa: BLE001
                # float16 在 CPU 上不支持，自动降级
                msg = str(exc).lower()
                if ("float16" in msg or "compute type" in msg) and "snapshot" not in msg:
                    if progress:
                        progress(0.0, f"{compute} 不可用，降级为 int8")
                    self._model = WhisperModel(
                        self.model_path, **{**kwargs, "compute_type": "int8"}
                    )
                    compute = "int8"
                elif device == "cuda" and "snapshot" not in msg:
                    if progress:
                        progress(0.0, f"CUDA 初始化失败，回退 CPU: {str(exc)[:80]}")
                    self._model = WhisperModel(
                        self.model_path,
                        **{**kwargs, "device": "cpu", "compute_type": "int8"},
                    )
                    device, compute = "cpu", "int8"
                else:
                    raise AsrError(_friendly_load_error(exc, self.model_path)) from exc

        self.resolved_device, self.resolved_compute = device, compute
        log.info("模型加载完成 %.1fs (%s/%s)", time.time() - t0, device, compute)
        if progress:
            progress(0.0, f"模型就绪 {device}/{compute}（{time.time() - t0:.1f}s）")

    def _resolve_device_compute(self) -> tuple[str, str]:
        opts = self.options
        device = (opts.device or "auto").lower()
        compute = (opts.compute_type or "auto").lower()
        if compute not in VALID_COMPUTE:
            compute = "auto"

        cuda_count = 0
        try:
            import ctranslate2

            cuda_count = ctranslate2.get_cuda_device_count()
        except Exception:  # noqa: BLE001
            cuda_count = 0

        if device == "auto":
            device = "cuda" if cuda_count > 0 else "cpu"

        if device == "cuda" and cuda_count == 0:
            device = "cpu"

        if compute == "auto":
            compute = "float16" if device == "cuda" else "int8"

        # Blackwell (sm_120，RTX 50 系) 上 INT8 被 ctranslate2 禁用，必须用 float16。
        if device == "cuda" and compute.startswith("int8") and _gpu_is_blackwell():
            log.warning("检测到 Blackwell GPU（RTX 50 系），INT8 不可用，自动降级为 float16")
            compute = "float16"

        # CPU 不支持 float16
        if device == "cpu" and compute in {"float16", "int8_float16"}:
            compute = "int8_float32" if compute == "int8_float16" else "int8"

        return device, compute

    # ------------------------------------------------------------------
    # 转录
    # ------------------------------------------------------------------
    def transcribe(
        self,
        audio_path: str | Path,
        progress: ProgressFn | None = None,
    ) -> AsrResult:
        opts = self.options
        # 取消检查：加载模型前
        if opts.cancel_token is not None and opts.cancel_token.is_set():
            raise TranscriptionCancelled()

        self._load(progress)
        assert self._model is not None

        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise AsrError(f"音频文件不存在: {audio_path}")

        # 取消检查：模型加载后
        if opts.cancel_token is not None and opts.cancel_token.is_set():
            raise TranscriptionCancelled()

        kwargs: dict = dict(
            language=opts.language,
            task=opts.task,
            beam_size=opts.beam_size,
            best_of=opts.best_of,
            temperature=opts.temperature,
            patience=opts.patience,
            length_penalty=opts.length_penalty,
            repetition_penalty=opts.repetition_penalty,
            no_repeat_ngram_size=opts.no_repeat_ngram_size,
            compression_ratio_threshold=opts.compression_ratio_threshold,
            log_prob_threshold=opts.log_prob_threshold,
            no_speech_threshold=opts.no_speech_threshold,
            condition_on_previous_text=opts.condition_on_previous_text,
            prompt_reset_on_temperature=opts.prompt_reset_on_temperature,
            word_timestamps=opts.word_timestamps,
            vad_filter=opts.vad_filter,
        )
        if opts.initial_prompt:
            kwargs["initial_prompt"] = opts.initial_prompt
        if opts.hotwords:
            kwargs["hotwords"] = opts.hotwords
        if opts.vad_filter and opts.vad_parameters:
            kwargs["vad_parameters"] = _clean_vad(opts.vad_parameters)

        # faster-whisper 的 hotwords 在旧版本不存在，做兼容剔除
        # 注意：transcribe() 同步执行 VAD 检测 + 音频编码，此阶段无法中断，但前后都检查取消
        if progress:
            if opts.vad_filter:
                dur = _wav_duration(audio_path)
                if dur > 120:
                    # VAD 用 onnxruntime CPU 单线程，长音频检测很慢（约 0.05-0.08x 实时）
                    progress(0.0, f"VAD 检测中（{dur:.0f}s 音频，约需 {dur * 0.07:.0f}s，此阶段无法中断）")
                else:
                    progress(0.0, "VAD 检测与音频编码中…")
            else:
                progress(0.0, "音频编码中…")
        try:
            iterator, info = self._model.transcribe(str(audio_path), **kwargs)
        except TypeError as exc:
            if "hotwords" in str(exc):
                kwargs.pop("hotwords", None)
                iterator, info = self._model.transcribe(str(audio_path), **kwargs)
            else:
                raise AsrError(f"转录参数错误: {exc}") from exc
        except TranscriptionCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AsrError(f"转录失败: {exc}") from exc

        # 取消检查：VAD/编码完成后（若这期间点了取消，立即生效）
        if opts.cancel_token is not None and opts.cancel_token.is_set():
            raise TranscriptionCancelled()

        total = float(getattr(info, "duration", 0.0) or 0.0)
        span_lo, span_hi = opts.progress_span
        segments: list[Segment] = []
        t0 = time.time()

        try:
            for seg in iterator:
                # 用户取消：直接冒泡，不能被下面的 except 吞掉
                if opts.cancel_token is not None and opts.cancel_token.is_set():
                    raise TranscriptionCancelled()
                if progress and total > 0:
                    frac = min(0.995, float(seg.end) / total)
                    pct = span_lo + (span_hi - span_lo) * frac
                    done = seg.end
                    speed = done / max(0.1, time.time() - t0)
                    eta = (total - done) / speed if speed > 0 else 0
                    progress(
                        pct,
                        f"转录 {done:.0f}/{total:.0f}s · {speed:.1f}x · 剩约 {eta:.0f}s",
                    )

                words = [
                    Word(
                        start=float(w.start),
                        end=float(w.end),
                        text=w.word,
                        probability=float(getattr(w, "probability", 1.0) or 1.0),
                    )
                    for w in (getattr(seg, "words", None) or [])
                    if w.start is not None and w.end is not None
                ]
                segments.append(
                    Segment(
                        start=float(seg.start),
                        end=float(seg.end),
                        text=(seg.text or "").strip(),
                        words=words,
                        avg_logprob=float(getattr(seg, "avg_logprob", 0.0) or 0.0),
                        no_speech_prob=float(getattr(seg, "no_speech_prob", 0.0) or 0.0),
                        source="asr",
                    )
                )
        except TranscriptionCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AsrError(_friendly_transcribe_error(exc, device, compute)) from exc

        if progress:
            progress(span_hi, f"转录完成 · {len(segments)} 段 · 耗时 {time.time() - t0:.0f}s")

        return AsrResult(
            segments=segments,
            language=str(getattr(info, "language", "") or ""),
            language_probability=float(getattr(info, "language_probability", 0.0) or 0.0),
            duration=total,
            engine=self.name,
        )

    def close(self) -> None:
        self._model = None
        try:
            import gc

            gc.collect()
        except Exception:  # noqa: BLE001
            pass

    def describe(self) -> str:
        name = Path(self.model_path).name if Path(self.model_path).exists() else self.model_path
        dev = f"{self.resolved_device}/{self.resolved_compute}" if self.resolved_device else "未加载"
        return f"faster-whisper · {name} · {dev} · 来源:{self.path_source}"


def _clean_vad(params: dict) -> dict:
    """过滤掉 None 值，转成 faster-whisper 认识的键。"""
    alias = {
        "threshold": "threshold",
        "min_speech_duration_ms": "min_speech_duration_ms",
        "max_speech_duration_s": "max_speech_duration_s",
        "min_silence_duration_ms": "min_silence_duration_ms",
        "speech_pad_ms": "speech_pad_ms",
    }
    out = {}
    for k, v in params.items():
        if v is None or k not in alias:
            continue
        out[alias[k]] = v
    return out


def _friendly_load_error(exc: Exception, model: str) -> str:
    """把 HF 的晦涩报错翻译成人话。"""
    msg = str(exc)
    low = msg.lower()
    if "snapshot" in low or "localentrynotfound" in low or "connection" in low:
        return (
            f"找不到本地模型「{model}」，且无法从 HuggingFace 下载。\n"
            "解决办法（任选其一）：\n"
            "  1. 运行  python scripts/download_models.py  让它从镜像下载\n"
            "  2. 手动把 faster-whisper 模型文件夹放进 models/asr/，并在 config.yaml 中填写路径\n"
            "  3. 在 config.yaml 的 asr.model 里填模型目录的绝对路径"
        )
    if "cublas" in low or "cudnn" in low or ("library" in low and "cuda" in low):
        return (
            f"CUDA 运行库缺失：{msg[:200]}\n"
            "请执行:  pip install nvidia-cublas-cu12 nvidia-cudnn-cu12\n"
            "或在 config.yaml 里设 asr.device: cpu 走 CPU"
        )
    return f"加载模型失败: {msg[:400]}"


def _friendly_transcribe_error(exc: Exception, device: str, compute: str) -> str:
    """转录阶段的错误友好化（尤其 cublas / Blackwell INT8 问题）。"""
    msg = str(exc)
    low = msg.lower()
    if "cublas" in low or "cudnn" in low:
        if compute.startswith("int8") and device == "cuda":
            return (
                f"GPU 转录失败：当前计算精度 {compute} 在 Blackwell（RTX 50 系）上不可用。\n"
                "请把「计算精度」改为 float16（在转录页或 config.yaml 的 asr.compute_type）。\n"
                "程序已尝试自动降级，若仍失败请手动修改。"
            )
        return (
            f"CUDA 运行库缺失或损坏：{msg[:200]}\n"
            "请执行:  pip install nvidia-cublas-cu12 nvidia-cudnn-cu12\n"
            "或在 config.yaml 里设 asr.device: cpu 走 CPU"
        )
    return f"转录失败: {msg[:400]}"


@contextlib.contextmanager
def _suppress_native_stderr():
    """临时吞掉 C 层 stderr 输出（CTranslate2 加载时很吵）。"""
    try:
        saved = sys.stderr
        sys.stderr = _io.StringIO()
        yield
    finally:
        sys.stderr = saved
