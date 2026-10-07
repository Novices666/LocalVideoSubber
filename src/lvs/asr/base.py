"""ASR 引擎抽象层。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..config import Config
from ..subtitle.models import Segment

ProgressFn = Callable[[float, str], None]


class AsrError(Exception):
    """ASR 相关错误。"""


class TranscriptionCancelled(Exception):
    """转录被用户取消。由上层转换成 jobs.TaskCancelled。"""


@dataclass
class AsrOptions:
    """引擎无关的转录参数。"""

    language: str | None = None          # None / "auto" 表示自动检测
    task: str = "transcribe"             # transcribe | translate
    beam_size: int = 5
    best_of: int = 5
    temperature: list[float] = field(default_factory=lambda: [0.0])
    patience: float = 1.0
    length_penalty: float = 1.0
    repetition_penalty: float = 1.0
    no_repeat_ngram_size: int = 0
    compression_ratio_threshold: float = 2.4
    log_prob_threshold: float = -1.0
    no_speech_threshold: float = 0.6
    condition_on_previous_text: bool = True
    prompt_reset_on_temperature: float = 0.5
    initial_prompt: str | None = None
    hotwords: str | None = None
    word_timestamps: bool = True
    vad_filter: bool = True
    vad_parameters: dict[str, Any] = field(default_factory=dict)
    device: str = "auto"
    compute_type: str = "float16"
    cpu_threads: int = 0
    num_workers: int = 1
    # 进度回调区间（长视频分段时，每段占用总进度的一部分）
    progress_span: tuple[float, float] = (0.0, 1.0)
    # 取消令牌（鸭子类型：只要有 is_set() 即可）
    cancel_token: Any = None

    @classmethod
    def from_config(cls, cfg: Config) -> "AsrOptions":
        lang = cfg.get("asr.language", "auto")
        if isinstance(lang, str) and lang.lower() in {"auto", "", "none"}:
            lang = None
        temp = cfg.get("asr.temperature", [0.0])
        if not isinstance(temp, list):
            temp = [float(temp)]
        return cls(
            language=lang,
            task=str(cfg.get("asr.task", "transcribe")),
            beam_size=int(cfg.get("asr.beam_size", 5)),
            best_of=int(cfg.get("asr.best_of", 5)),
            temperature=[float(t) for t in temp],
            patience=float(cfg.get("asr.patience", 1.0)),
            length_penalty=float(cfg.get("asr.length_penalty", 1.0)),
            repetition_penalty=float(cfg.get("asr.repetition_penalty", 1.0)),
            no_repeat_ngram_size=int(cfg.get("asr.no_repeat_ngram_size", 0)),
            compression_ratio_threshold=float(cfg.get("asr.compression_ratio_threshold", 2.4)),
            log_prob_threshold=float(cfg.get("asr.log_prob_threshold", -1.0)),
            no_speech_threshold=float(cfg.get("asr.no_speech_threshold", 0.6)),
            condition_on_previous_text=bool(cfg.get("asr.condition_on_previous_text", True)),
            prompt_reset_on_temperature=float(cfg.get("asr.prompt_reset_on_temperature", 0.5)),
            initial_prompt=cfg.get("asr.initial_prompt") or None,
            hotwords=cfg.get("asr.hotwords") or None,
            word_timestamps=bool(cfg.get("asr.word_timestamps", True)),
            vad_filter=bool(cfg.get("asr.vad_filter", True)),
            vad_parameters=cfg.section("asr").get("vad_parameters") or {},
            device=str(cfg.get("asr.device", "auto")),
            compute_type=str(cfg.get("asr.compute_type", "float16")),
            cpu_threads=int(cfg.get("asr.cpu_threads", 0) or 0),
            num_workers=int(cfg.get("asr.num_workers", 1) or 1),
        )


@dataclass
class AsrResult:
    segments: list[Segment]
    language: str = ""
    language_probability: float = 0.0
    duration: float = 0.0
    engine: str = ""

    @property
    def text(self) -> str:
        return "\n".join(s.text for s in self.segments)


class AsrEngine(ABC):
    """ASR 引擎接口。"""

    name: str = "base"

    def __init__(self, cfg: Config, options: AsrOptions | None = None) -> None:
        self.cfg = cfg
        self.options = options or AsrOptions.from_config(cfg)

    @abstractmethod
    def transcribe(
        self,
        audio_path: str | Path,
        progress: ProgressFn | None = None,
    ) -> AsrResult:
        """转录单段音频（16k 单声道 wav）。"""

    def close(self) -> None:
        """释放模型。"""

    def describe(self) -> str:
        return self.name


# --------------------------------------------------------------------------
# 引擎工厂
# --------------------------------------------------------------------------
def create_engine(
    cfg: Config,
    engine: str | None = None,
    options: AsrOptions | None = None,
    model_path: str | None = None,
) -> AsrEngine:
    """按配置创建引擎。model_path 为调用方显式指定的覆盖值。"""
    from ..config import resolve_model_path

    engine = (engine or cfg.get("asr.engine", "faster-whisper")).lower()
    options = options or AsrOptions.from_config(cfg)

    if engine in {"whispercpp", "whisper.cpp", "cpp"}:
        from .whispercpp_engine import WhisperCppEngine

        path, source = resolve_model_path(cfg, "whispercpp", model_path)
        return WhisperCppEngine(cfg, options, model_path=path, path_source=source)

    from .faster_whisper_engine import FasterWhisperEngine

    path, source = resolve_model_path(cfg, "asr", model_path)
    return FasterWhisperEngine(cfg, options, model_path=path, path_source=source)
