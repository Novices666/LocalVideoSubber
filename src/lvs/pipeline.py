"""流水线编排：把 ASR / 翻译 / 渲染串成可被任务队列调用的 runner。

每种 Job kind 对应一个 runner，签名 (Job, JobContext) -> dict。
"""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from .asr.base import (
    AsrError,
    AsrOptions,
    TranscriptionCancelled,
    create_engine,
)
from .asr.diarization import (
    run_diarization,
    speaker_boundaries,
    split_segments_by_speakers,
)
from .config import Config, merge_overrides, resolve_model_path
from .jobs import Job, JobContext, TaskCancelled
from .media import (
    Cancelled,
    MediaError,
    extract_audio,
    plan_chunks,
    probe,
    safe_stem,
    slice_audio,
)
from .subtitle.io import load_subtitle, save_subtitle
from .subtitle.models import Cue, Segment, reindex, stats
from .subtitle.render import (
    RenderOptions,
    RenderError,
    burn_subtitles,
    export_subtitles,
    mux_subtitles,
)
from .subtitle.segmenter import SegmentOptions, segments_to_cues
from .translate.llm_client import LlmClient, LlmConfig, LlmError
from .translate.pipeline import SubtitleTranslator, TranslateOptions

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# 辅助
# --------------------------------------------------------------------------
def _work_dir(cfg: Config, stem: str, job: Job) -> Path:
    d = cfg.output_dir / stem / job.id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _to_cancelled(exc: Exception) -> None:
    """把 media.Cancelled 统一成 TaskCancelled。"""
    if isinstance(exc, (Cancelled,)) or type(exc).__name__ == "Cancelled":
        raise TaskCancelled()


def _progress_with_log(
    ctx: JobContext, lo: float, hi: float, throttle: float = 3.0
) -> Callable[[float, str], None]:
    """进度回调：更新进度条的同时写日志。

    含 "/" 的高频进度（如"烧录 12/345s"）按 throttle 秒节流，
    避免每帧刷爆日志；阶段消息（无 "/"）总是写日志。
    """
    last = {"t": 0.0}

    def fn(p: float, msg: str = "") -> None:
        ctx.progress(lo + (hi - lo) * max(0.0, min(1.0, p)), msg)
        if not msg:
            return
        now = time.time()
        if "/" in msg and now - last["t"] < throttle:
            return
        ctx.log(msg)
        last["t"] = now

    return fn


def _cleanup(
    cfg: Config,
    work: Path,
    ctx: JobContext,
    keep: bool,
    preserve: list[Path] | None = None,
) -> None:
    if keep:
        ctx.log(f"中间产物保留在 {work}")
        return
    try:
        keep_paths = {p.resolve() for p in (preserve or []) if p.exists()}
        if not keep_paths:
            shutil.rmtree(work, ignore_errors=True)
            return
        for child in work.iterdir():
            if child.resolve() in keep_paths:
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------
# Runner: 转录
# --------------------------------------------------------------------------
def run_transcribe(job: Job, ctx: JobContext) -> dict[str, Any]:
    """payload:
        video: str            输入视频/音频
        model_path: str|None  调用方显式指定的模型路径
        engine: str|None      faster-whisper | whispercpp
        language, task, ...
        seg_opts: dict        智能断句覆盖
    """
    cfg: Config = job.payload["cfg"]
    video = Path(job.payload["video"])
    overrides = job.payload.get("overrides") or {}
    cfg = merge_overrides(cfg, overrides) if overrides else cfg

    stem = safe_stem(video.name)
    work = _work_dir(cfg, stem, job)
    keep = bool(cfg.get("jobs.keep_intermediate", True))

    result: dict[str, Any] = {"work_dir": str(work), "source": str(video)}
    preserve_files: list[Path] = []

    engine = None
    try:
        # ---- 1. 探测 (0 - 0.05)
        ctx.stage("探测媒体", 0.0, "读取媒体信息…")
        info = probe(video)
        if not info.has_audio:
            raise MediaError(f"文件没有音频流，无法转录：{video.name}")
        ctx.log(f"媒体: {info.summary()}")
        result["media"] = {
            "duration": info.duration,
            "has_video": info.has_video,
            "resolution": info.resolution,
            "fps": info.fps,
            "video_codec": info.video_codec,
        }
        ctx.progress(0.05, f"时长 {info.duration_text}")

        # ---- 2. 抽音频 (0.05 - 0.15)
        ctx.stage("抽取音频", 0.05)
        wav = work / "audio.wav"
        if not wav.exists() or wav.stat().st_size < 1024:
            try:
                extract_audio(
                    video, wav,
                    progress=ctx.scaled(0.05, 0.15),
                    cancel=ctx.cancel,
                    info=info,
                )
            except Cancelled:
                raise TaskCancelled()
            except MediaError as exc:
                raise MediaError(str(exc)) from exc
        else:
            ctx.progress(0.15, "复用已有音频")
        ctx.log(f"音频: {wav.name} ({wav.stat().st_size / 1024 / 1024:.1f} MB)")

        # ---- 3. 转录 (0.15 - 0.80)
        ctx.stage("语音转录", 0.15)
        asr_opts = AsrOptions.from_config(cfg)
        asr_opts.cancel_token = ctx.cancel   # 让引擎自身能检查取消
        seg_cfg = cfg.section("segment")
        model_path = job.payload.get("model_path")
        engine_name = job.payload.get("engine") or cfg.get("asr.engine")

        engine = create_engine(cfg, engine_name, asr_opts, model_path)
        ctx.log(f"引擎: {engine.describe()}")

        threshold = float(cfg.get("jobs.segment_threshold", 600) or 0)
        chunks = plan_chunks(info.duration, threshold)

        # 长视频分段时，每段 VAD 检测（onnxruntime CPU 单线程）要 40-50 秒，
        # 4 段就是 3 分钟纯 VAD，且该阶段无法取消。分段本身已按时间切分，
        # VAD 静音过滤价值低，自动关闭以加速并让取消能及时生效。
        if len(chunks) > 1 and asr_opts.vad_filter:
            asr_opts.vad_filter = False
            ctx.log("长视频分段处理：已自动关闭 VAD 静音过滤（分段已按时间切分，且 VAD 检测很慢）")

        all_segments: list[Segment] = []
        detected_lang = ""

        if len(chunks) == 1:
            asr_opts.progress_span = (0.15, 0.78)
            res = _transcribe_one(engine, wav, asr_opts, ctx)
            all_segments = res.segments
            detected_lang = res.language
        else:
            ctx.log(f"长视频分段处理：{len(chunks)} 段（每段约 {chunks[0].duration / 60:.0f} 分钟）")
            for i, chunk in enumerate(chunks):
                ctx.check()
                lo = 0.15 + (0.63 * i / len(chunks))
                hi = 0.15 + (0.63 * (i + 1) / len(chunks))
                asr_opts.progress_span = (lo, hi)
                ctx.log(f"第 {i + 1}/{len(chunks)} 段（{chunk.start:.0f}s ~ {chunk.end:.0f}s）")
                ctx.log(f"正在切分第 {i + 1}/{len(chunks)} 段音频…")
                ctx.progress(lo, f"切分第 {i + 1}/{len(chunks)} 段")
                piece = slice_audio(wav, chunk, work / "chunks", cancel=ctx.cancel)
                ctx.log(f"第 {i + 1}/{len(chunks)} 段切分完成，开始转录")
                res = _transcribe_one(engine, piece, asr_opts, ctx)
                ctx.log(f"第 {i + 1}/{len(chunks)} 段转录完成（{len(res.segments)} 段）")
                if not detected_lang:
                    detected_lang = res.language
                for s in res.segments:
                    all_segments.append(
                        Segment(
                            start=s.start + chunk.start,
                            end=s.end + chunk.start,
                            text=s.text,
                            words=[
                                type(w)(start=w.start + chunk.start, end=w.end + chunk.start,
                                        text=w.text, probability=w.probability)
                                for w in s.words
                            ],
                            avg_logprob=s.avg_logprob,
                            no_speech_prob=s.no_speech_prob,
                            source="asr",
                        )
                    )

        result["language"] = detected_lang
        result["raw_segments"] = len(all_segments)
        ctx.log(f"原始分段 {len(all_segments)} 段，语言 {detected_lang or '未知'}")

        # ---- 4. 智能断句 (0.80 - 0.88)
        ctx.stage("智能断句", 0.80)
        segments = [s for s in all_segments if s.text.strip()]
        if not segments:
            raise MediaError("没有识别到任何语音内容（可能是纯音乐或静音）")

        # 4.1 说话人分离（可选）：按说话人切换点切分分段，实现自动换行
        if cfg.get("asr.speaker_diarization", False):
            ctx.log("说话人分离：开始分析…")
            diar = run_diarization(str(wav))
            if diar.ok:
                bounds = speaker_boundaries(diar.turns)
                before = len(segments)
                segments = split_segments_by_speakers(segments, bounds)
                ctx.log(
                    f"说话人分离：{diar.num_speakers} 人，"
                    f"{len(bounds)} 个切换点，{before} -> {len(segments)} 段"
                )
            else:
                ctx.log("说话人分离：未检测到有效说话人，跳过", "warn")

        sopts = SegmentOptions.from_config(cfg)
        for k, v in (job.payload.get("seg_opts") or {}).items():
            if v is not None and hasattr(sopts, k):
                setattr(sopts, k, v)
        cues = segments_to_cues(segments, sopts)
        ctx.log(f"断句后 {len(cues)} 条字幕（平均 {stats(cues)['avg_chars']} 字/条）")

        # ---- 5. 落盘 (0.88 - 1.0)
        ctx.stage("写出字幕", 0.88)
        raw_srt = work / "raw.srt"
        save_subtitle(
            [Cue(index=i, start=s.start, end=s.end, text=s.text)
             for i, s in enumerate(all_segments, 1)],
            raw_srt, mode="source",
        )
        out_srt = work / f"{stem}.srt"
        preserve_files.append(out_srt)
        save_subtitle(cues, out_srt, mode="source")
        ctx.log(f"原始分段 -> {raw_srt.name}")
        ctx.log(f"断句结果 -> {out_srt.name}")

        result.update({
            "cues": [c.to_dict() for c in cues],
            "srt": str(out_srt),
            "raw_srt": str(raw_srt),
            "wav": str(wav),
            "stats": stats(cues),
        })
        ctx.progress(1.0, f"完成 · {len(cues)} 条字幕")

    finally:
        if engine is not None:
            try:
                engine.close()
            except Exception as exc:  # noqa: BLE001
                ctx.log(f"释放识别模型失败：{exc}", "warn")
        _cleanup(cfg, work, ctx, keep, preserve_files)

    return result


def _transcribe_one(engine, wav: Path, opts: AsrOptions, ctx: JobContext):
    """转录单段音频。把引擎的阶段性 message 也写进日志（进度性消息节流），避免日志停在某一步。"""
    last_msg = [""]
    last_progress_log = [0.0]

    def on_progress(p: float, m: str = "") -> None:
        ctx.progress(p, m)
        if m and m != last_msg[0]:
            now = time.time()
            is_progress = ("转录 " in m) or ("x ·" in m) or ("剩约" in m)
            if not is_progress or now - last_progress_log[0] >= 3.0:
                ctx.log(m)
                last_progress_log[0] = now
            last_msg[0] = m

    try:
        return engine.transcribe(wav, progress=on_progress)
    except (Cancelled, TaskCancelled):
        raise TaskCancelled()
    except AsrError:
        raise
    except Exception as exc:  # noqa: BLE001
        _to_cancelled(exc)
        # 取消信号可能被引擎内部包装过，识别名字再转一次
        if "cancel" in type(exc).__name__.lower() or ctx.cancel.is_set():
            raise TaskCancelled()
        raise AsrError(f"转录失败: {exc}") from exc


# --------------------------------------------------------------------------
# Runner: 翻译
# --------------------------------------------------------------------------
def run_translate(job: Job, ctx: JobContext) -> dict[str, Any]:
    """翻译任务外壳，确保失败/取消时也清理中间目录。"""
    cfg: Config = job.payload["cfg"]
    overrides = job.payload.get("overrides") or {}
    effective_cfg = merge_overrides(cfg, overrides) if overrides else cfg
    stem = job.payload.get("stem", "subtitle")
    work = Path(job.payload.get("work_dir") or _work_dir(effective_cfg, stem, job))
    keep = bool(effective_cfg.get("jobs.keep_intermediate", True))
    result: dict[str, Any] | None = None
    try:
        result = _run_translate_impl(job, ctx)
        return result
    finally:
        preserve = []
        if result:
            preserve = [
                Path(result[key])
                for key in ("bilingual_srt", "target_srt")
                if result.get(key)
            ]
        _cleanup(effective_cfg, work, ctx, keep, preserve)


def _run_translate_impl(job: Job, ctx: JobContext) -> dict[str, Any]:
    """payload:
        cues: list[dict]       要翻译的字幕
        overrides: dict        translate.* 覆盖
        stem / work_dir
    """
    cfg: Config = job.payload["cfg"]
    overrides = job.payload.get("overrides") or {}
    cfg = merge_overrides(cfg, overrides) if overrides else cfg
    stem = job.payload.get("stem", "subtitle")
    work = Path(job.payload.get("work_dir") or _work_dir(cfg, stem, job))
    work.mkdir(parents=True, exist_ok=True)
    keep = bool(cfg.get("jobs.keep_intermediate", True))

    cues = [Cue.from_dict(d) for d in job.payload["cues"]]
    if not cues:
        raise LlmError("没有可翻译的字幕")

    ctx.stage("连接翻译服务", 0.0, "检查 LLM 服务…")
    llm_cfg = LlmConfig.from_config(cfg)
    client = LlmClient(llm_cfg)

    ok, detail = client.ping(timeout=6.0)
    if not ok:
        raise LlmError(detail)
    ctx.log(f"翻译服务: {detail}")

    top = TranslateOptions.from_config(cfg)
    actual_concurrency = 1 if top.context_size > 0 else top.concurrency
    ctx.log(
        f"策略: {top.batch_size} 条/批 · 上下文 {top.context_size} 条 · "
        f"并发 {actual_concurrency} · {top.source_lang} → {top.target_lang}"
    )
    if actual_concurrency != top.concurrency:
        ctx.log("已启用上下文顺序保护，并发请求自动降为 1", "warn")

    ctx.stage("批量翻译", 0.02)
    translator = SubtitleTranslator(client, top, llm_cfg)

    def _log_progress(p: float, msg: str = "") -> None:
        """进度回调：更新进度条的同时写入日志（每批完成可见）。"""
        ctx.progress(0.02 + 0.93 * max(0.0, min(1.0, p)), msg)
        if msg:
            ctx.log(msg)

    try:
        res = translator.translate_cues(
            cues,
            progress=_log_progress,
            cancel=ctx.cancel,
        )
    except Cancelled:
        raise TaskCancelled()
    except Exception as exc:  # noqa: BLE001
        # llm_client 流式取消（_Cancelled）或用户已点取消 -> 统一转取消
        if type(exc).__name__ == "_Cancelled" or ctx.cancel.is_set():
            raise TaskCancelled()
        raise
    finally:
        client.close()

    ctx.log(
        f"成功 {res.translated}/{res.total} 条 · {res.batches} 批"
        + (f" · 重试 {res.retries} 次" if res.retries else "")
    )
    if res.failed:
        ctx.log(f"未翻译条目: {res.failed[:20]}{' …' if len(res.failed) > 20 else ''}", "warn")

    ctx.stage("写出文件", 0.95)
    out = work / f"{stem}.bilingual.srt"
    save_subtitle(cues, out, mode="both")
    out_t = work / f"{stem}.zh.srt"
    save_subtitle(cues, out_t, mode="target")
    ctx.log(f"双语 -> {out.name}")

    ctx.progress(1.0, f"翻译完成 · {res.translated} 条")
    return {
        "cues": [c.to_dict() for c in cues],
        "translated": res.translated,
        "failed": res.failed,
        "batches": res.batches,
        "retries": res.retries,
        "bilingual_srt": str(out),
        "target_srt": str(out_t),
        "work_dir": str(work),
        "stats": stats(cues),
    }


# --------------------------------------------------------------------------
# Runner: 渲染
# --------------------------------------------------------------------------
def run_render(job: Job, ctx: JobContext) -> dict[str, Any]:
    """payload:
        cues, video(optional), mode, overrides(render.*), output_name
    """
    cfg: Config = job.payload["cfg"]
    overrides = job.payload.get("overrides") or {}
    cfg = merge_overrides(cfg, overrides) if overrides else cfg

    cues = [Cue.from_dict(d) for d in job.payload["cues"]]
    if not cues:
        raise RenderError("没有可渲染的字幕")

    mode = job.payload.get("mode", "burn")
    video = job.payload.get("video")
    stem = job.payload.get("stem") or (
        safe_stem(Path(video).name) if video else "subtitle"
    )
    out_dir = Path(job.payload.get("out_dir") or cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ropts = RenderOptions.from_config(cfg)
    ropts.mode = mode
    ropts.display_mode = job.payload.get("display_mode") or ropts.display_mode
    ropts.order = job.payload.get("order") or ropts.order
    if job.payload.get("encoder"):
        ropts.encoder = job.payload["encoder"]
    if job.payload.get("crf") is not None:
        ropts.crf = int(job.payload["crf"])
    if job.payload.get("container"):
        ropts.container = job.payload["container"]

    ctx.stage("准备渲染", 0.0, f"模式: {mode}")

    if mode == "export":
        ctx.progress(0.3, "导出字幕文件…")
        paths = export_subtitles(
            cues, out_dir, stem,
            formats=job.payload.get("formats") or ["srt", "vtt", "ass"],
            mode=ropts.display_mode,
            order=ropts.order,
            style=ropts.style,
            bilingual=ropts.bilingual,
        )
        ctx.progress(1.0, f"导出 {len(paths)} 个文件")
        for p in paths:
            ctx.log(f"  {p.name}")
        return {
            "mode": "export",
            "files": [str(p) for p in paths],
            "primary": str(paths[0]) if paths else "",
            "output": str(paths[0]) if paths else "",
        }

    if not video:
        raise RenderError(f"{mode} 模式需要原始视频")
    video = Path(video)
    if not video.exists():
        raise RenderError(f"视频不存在: {video}")

    if mode == "soft":
        out = out_dir / f"{stem}.subbed.{ropts.container}"
        ctx.stage("封装字幕轨", 0.05)
        try:
            res = mux_subtitles(
                video, cues, out, ropts,
                progress=_progress_with_log(ctx, 0.05, 0.98), cancel=ctx.cancel,
            )
        except Cancelled:
            raise TaskCancelled()
        ctx.log(f"输出: {res.output.name} ({res.size_mb} MB)")
        ctx.progress(1.0, res.note)
        return {
            "mode": "soft", "output": str(res.output),
            "size_mb": res.size_mb, "note": res.note,
        }

    if mode == "burn":
        out = out_dir / f"{stem}.subbed.mp4"
        ctx.stage("烧录字幕", 0.05)
        try:
            res = burn_subtitles(
                video, cues, out, ropts,
                progress=_progress_with_log(ctx, 0.05, 0.98), cancel=ctx.cancel,
            )
        except Cancelled:
            raise TaskCancelled()
        ctx.log(f"输出: {res.output.name} ({res.size_mb} MB)")
        ctx.progress(1.0, res.note)
        return {
            "mode": "burn", "output": str(res.output),
            "size_mb": res.size_mb, "note": res.note,
        }

    raise RenderError(f"未知模式: {mode}")


# --------------------------------------------------------------------------
# Runner: 只做断句（从已有字幕重切）
# --------------------------------------------------------------------------
def run_resegment(job: Job, ctx: JobContext) -> dict[str, Any]:
    cfg: Config = job.payload["cfg"]
    cues = [Cue.from_dict(d) for d in job.payload["cues"]]
    ctx.stage("重新断句", 0.0)
    segs = [
        Segment(start=c.start, end=c.end, text=c.text, source="manual")
        for c in cues
    ]
    sopts = SegmentOptions.from_config(cfg)
    for k, v in (job.payload.get("seg_opts") or {}).items():
        if v is not None and hasattr(sopts, k):
            setattr(sopts, k, v)
    out = segments_to_cues(segs, sopts)
    ctx.progress(1.0, f"{len(cues)} 条 -> {len(out)} 条")
    ctx.log(f"重新断句完成：{len(cues)} 条 -> {len(out)} 条")
    return {"cues": [c.to_dict() for c in out], "stats": stats(out)}


# --------------------------------------------------------------------------
# 注册到队列
# --------------------------------------------------------------------------
def register_all(q) -> None:
    q.register("transcribe", run_transcribe)
    q.register("translate", run_translate)
    q.register("render", run_render)
    q.register("resegment", run_resegment)
