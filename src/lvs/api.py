"""FastAPI 应用：把 pipeline / jobs / styles / workspace 暴露为 REST + SSE。

前端（React + Vite）通过此 API 驱动整个流水线。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from . import styles as style_store
from .config import (
    PROJECT_ROOT,
    load_config,
    merge_overrides,
    run_startup_checks,
    save_config,
    scan_models,
    summarize_checks,
)
from .jobs import JobStatus, get_queue
from .media import list_media_files, probe, safe_stem
from .pipeline import register_all
from .subtitle.io import load_subtitle
from .subtitle.models import Cue
from .workspace import get_workspace

log = logging.getLogger(__name__)

WEB_DIST = PROJECT_ROOT / "web" / "dist"

# --------------------------------------------------------------------------
# 全局队列单例
# --------------------------------------------------------------------------
_queue = None
_queue_ready = False


def q():
    global _queue, _queue_ready
    if _queue is None:
        _queue = get_queue()
        register_all(_queue)
        _queue.on_update = get_workspace().update_from_job
        _queue.start()
        _queue_ready = True
    return _queue


def _cfg():
    return load_config()


# --------------------------------------------------------------------------
# 请求体模型
# --------------------------------------------------------------------------
class TranscribeRequest(BaseModel):
    video: str
    model_path: str | None = None
    engine: str = "faster-whisper"
    language: str = "auto"
    task: str = "transcribe"
    beam_size: int = 5
    device: str = "auto"
    compute_type: str = "int8_float16"
    vad_filter: bool = True
    initial_prompt: str = ""
    seg_opts: dict[str, Any] = Field(default_factory=dict)
    speaker_diarization: bool = False


class TranslateRequest(BaseModel):
    source_lang: str = "auto"
    target_lang: str = "zh"
    batch_size: int = 25
    context_size: int = 3
    concurrency: int = 1
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    temperature: float = 0.3
    disable_thinking: bool = True
    glossary: dict[str, str] = Field(default_factory=dict)
    system_prompt: str = ""


class RenderRequest(BaseModel):
    mode: str = "burn"
    display_mode: str = "both"
    encoder: str = "auto"
    crf: int = 20
    container: str = "mkv"
    style_profile: str = "标准样式"
    video: str = ""


class CuesUpload(BaseModel):
    cues: list[dict[str, Any]]


class ConfigUpdate(BaseModel):
    values: dict[str, Any]


class StyleUpdate(BaseModel):
    name: str
    font_name: str = "Microsoft YaHei"
    font_size: int = 42
    source_font_size: int = 32
    target_font_size: int = 42
    primary_color: str = "&H00FFFFFF"
    secondary_color: str = "&H000000FF"
    outline_color: str = "&H00000000"
    back_color: str = "&H80000000"
    outline: int = 2
    source_outline: int | None = None
    target_outline: int | None = None
    shadow: int = 1
    margin_v: int = 40
    source_margin_v: int | None = None
    target_margin_v: int | None = None
    order: str = "target_top"
    orientation: str = "landscape"


class LlmTestRequest(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""


# --------------------------------------------------------------------------
# 应用
# --------------------------------------------------------------------------
def create_app() -> FastAPI:
    app = FastAPI(title="LocalVideoSubber", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173",
                       "http://localhost:8000", "http://127.0.0.1:8000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ==============================================================
    # 系统状态
    # ==============================================================
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "time": time.time()}

    @app.get("/api/check")
    def check() -> dict[str, Any]:
        items = run_startup_checks(_cfg())
        ok, md = summarize_checks(items)
        return {
            "ok": ok,
            "markdown": md,
            "items": [
                {"name": i.name, "ok": i.ok, "detail": i.detail, "level": i.level}
                for i in items
            ],
        }

    # ==============================================================
    # 模型
    # ==============================================================
    @app.get("/api/models")
    def models() -> dict[str, Any]:
        return {
            "asr": scan_models(_cfg(), "asr"),
            "translate": scan_models(_cfg(), "translate"),
        }

    # ==============================================================
    # 媒体
    # ==============================================================
    @app.get("/api/media/probe")
    def media_probe(path: str = Query(...)) -> dict[str, Any]:
        try:
            info = probe(path)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc))
        return {
            "path": str(info.path),
            "duration": info.duration,
            "duration_text": info.duration_text,
            "has_video": info.has_video,
            "has_audio": info.has_audio,
            "resolution": info.resolution,
            "fps": info.fps,
            "video_codec": info.video_codec,
            "audio_codec": info.audio_codec,
            "sample_rate": info.sample_rate,
            "channels": info.channels,
            "container": info.container,
        }

    @app.get("/api/media/list")
    def media_list(folder: str = Query(...)) -> dict[str, Any]:
        files = list_media_files(folder)
        return {"files": [str(f) for f in files]}

    @app.post("/api/upload")
    async def upload(file: UploadFile = File(...)) -> dict[str, Any]:
        """拖拽/点击上传文件，保存到 output/uploads/。返回落盘路径。"""
        if not file.filename:
            raise HTTPException(status_code=400, detail="缺少文件名")
        upload_dir = _cfg().output_dir / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(file.filename).name
        dest = upload_dir / safe_name
        stem, suffix = dest.stem, dest.suffix
        i = 1
        while dest.exists():
            dest = upload_dir / f"{stem}_{i}{suffix}"
            i += 1
        size = 0
        with open(dest, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)
                size += len(chunk)
        return {"path": str(dest), "name": dest.name, "size": size}

    @app.get("/api/media/file")
    def media_file(path: str = Query(...)):
        """产物文件访问（支持 Range，用于视频预览）。仅限 output/ 与 models/ 目录。"""
        p = Path(path).resolve()
        allowed_roots = [_cfg().output_dir.resolve(), _cfg().model_root.resolve(), PROJECT_ROOT.resolve()]
        if not any(p == root or root in p.parents for root in allowed_roots):
            raise HTTPException(status_code=403, detail="路径不在允许范围内")
        if not p.is_file():
            raise HTTPException(status_code=404, detail="文件不存在")
        return FileResponse(p)

    # ==============================================================
    # LLM 连接测试
    # ==============================================================
    @app.post("/api/llm/test")
    def llm_test(req: LlmTestRequest) -> dict[str, Any]:
        from .translate.llm_client import LlmClient, LlmConfig

        if not req.base_url:
            return {"ok": False, "detail": "未填写服务地址"}
        client = LlmClient(LlmConfig(
            base_url=req.base_url, api_key=req.api_key or "", model=req.model or ""
        ))
        try:
            ok, detail = client.ping(timeout=6.0)
        finally:
            client.close()
        return {"ok": ok, "detail": detail}

    @app.post("/api/llm/models")
    def llm_models(req: LlmTestRequest) -> dict[str, Any]:
        """从 OpenAI 兼容服务拉取模型列表（供下拉选择）。"""
        from .translate.llm_client import LlmClient, LlmConfig

        if not req.base_url:
            return {"models": [], "detail": "未填写服务地址"}
        client = LlmClient(LlmConfig(
            base_url=req.base_url, api_key=req.api_key or "", model=req.model or ""
        ))
        try:
            models = client.list_models(timeout=6.0)
        except Exception as exc:  # noqa: BLE001
            return {"models": [], "detail": str(exc)}
        finally:
            client.close()
        return {"models": models, "detail": ""}

    # ==============================================================
    # 任务
    # ==============================================================
    def _submit(kind: str, title: str, payload: dict[str, Any]) -> dict[str, Any]:
        job = q().submit(kind, payload, title=title)
        return {"job_id": job.id, "kind": kind, "title": title}

    @app.post("/api/jobs/transcribe")
    def job_transcribe(req: TranscribeRequest) -> dict[str, Any]:
        if not req.video or not Path(req.video).exists():
            raise HTTPException(status_code=400, detail="视频文件不存在")
        overrides = {
            "asr.engine": req.engine,
            "asr.language": None if req.language == "auto" else req.language,
            "asr.task": req.task,
            "asr.beam_size": req.beam_size,
            "asr.device": req.device,
            "asr.compute_type": req.compute_type,
            "asr.vad_filter": req.vad_filter,
            "asr.initial_prompt": req.initial_prompt or None,
            "asr.speaker_diarization": req.speaker_diarization,
        }
        payload = {
            "cfg": _cfg(),
            "video": req.video,
            "model_path": req.model_path or None,
            "engine": req.engine,
            "overrides": overrides,
            "seg_opts": req.seg_opts,
        }
        ws = get_workspace()
        ws.set_video(req.video)
        return _submit("transcribe", f"转录 {Path(req.video).name}", payload)

    @app.post("/api/jobs/translate")
    def job_translate(req: TranslateRequest) -> dict[str, Any]:
        ws = get_workspace()
        cues = ws.cues_dicts()
        if not cues:
            raise HTTPException(status_code=400, detail="工作区没有字幕，请先转录或导入")
        overrides = {
            "translate.source_lang": req.source_lang,
            "translate.target_lang": req.target_lang,
            "translate.batch_size": req.batch_size,
            "translate.context_size": req.context_size,
            "translate.concurrency": req.concurrency,
            "translate.base_url": req.base_url or None,
            "translate.api_key": req.api_key,
            "translate.model": req.model or None,
            "translate.temperature": req.temperature,
            "translate.disable_thinking": req.disable_thinking,
            "translate.glossary": req.glossary,
            "translate.system_prompt": req.system_prompt or None,
        }
        payload = {
            "cfg": _cfg(),
            "cues": cues,
            "overrides": overrides,
            "stem": ws.video_stem or "subtitle",
            "work_dir": ws.work_dir,
        }
        return _submit("translate", f"翻译 {len(cues)} 条 → {req.target_lang}", payload)

    @app.post("/api/jobs/render")
    def job_render(req: RenderRequest) -> dict[str, Any]:
        ws = get_workspace()
        cues = ws.cues_dicts()
        if not cues:
            raise HTTPException(status_code=400, detail="工作区没有字幕，请先转录或导入")
        video = req.video or ws.video
        if req.mode in ("burn", "soft") and (not video or not Path(video).exists()):
            raise HTTPException(status_code=400, detail="该渲染模式需要原始视频")

        # 样式 profile → style + bilingual
        profile = style_store.get_style(req.style_profile)
        style, bilingual = profile.to_render()

        overrides = {
            "render.burn_encoder": req.encoder,
            "render.burn_crf": req.crf,
            "render.soft_container": req.container,
        }
        stem = safe_stem(Path(video).name) if video else "subtitle"
        payload = {
            "cfg": _cfg(),
            "cues": cues,
            "video": video,
            "mode": req.mode,
            "display_mode": req.display_mode,
            "encoder": req.encoder,
            "crf": req.crf,
            "container": req.container,
            "stem": stem,
            "out_dir": str(_cfg().output_dir),
            "overrides": overrides,
            "style": style,
            "bilingual": bilingual,
        }
        return _submit("render", f"渲染 {req.mode} {len(cues)} 条", payload)

    @app.post("/api/jobs/{job_id}/cancel")
    def job_cancel(job_id: str) -> dict[str, Any]:
        ok = q().cancel(job_id)
        return {"cancelled": ok, "job_id": job_id}

    @app.get("/api/jobs")
    def jobs() -> dict[str, Any]:
        rows = []
        for j in q().list_jobs(100):
            s = j.snapshot()
            rows.append({
                "id": s["id"], "kind": s["kind"], "title": s["title"],
                "status": s["status"], "status_tag": s["status_tag"],
                "stage": s["stage"], "progress": s["progress"],
                "message": s["message"], "elapsed": s["elapsed"],
                "error": s["error"],
            })
        return {"jobs": rows}

    @app.get("/api/jobs/{job_id}")
    def job_detail(job_id: str) -> dict[str, Any]:
        job = q().get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return job.snapshot()

    @app.get("/api/jobs/{job_id}/events")
    def job_events(job_id: str):
        """SSE 进度流。任务结束（完成/失败/取消）后主动关闭。"""
        job = q().get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="任务不存在")

        async def gen():
            last_seq = -1
            while True:
                snap = job.snapshot()
                seq = len(snap["logs"])
                if seq != last_seq:
                    last_seq = seq
                    yield {
                        "event": "progress",
                        "data": json.dumps({
                            "status": snap["status"],
                            "status_tag": snap["status_tag"],
                            "stage": snap["stage"],
                            "progress": snap["progress"],
                            "message": snap["message"],
                            "logs": snap["logs"],
                            "elapsed": snap["elapsed"],
                            "error": snap["error"],
                        }, ensure_ascii=False),
                    }
                if snap["status"] in (JobStatus.DONE.value, JobStatus.FAILED.value,
                                      JobStatus.CANCELLED.value):
                    yield {
                        "event": "done",
                        "data": json.dumps({"status": snap["status"],
                                            "result": snap["result"],
                                            "error": snap["error"]}, ensure_ascii=False),
                    }
                    break
                await __import__("asyncio").sleep(0.5)

        return EventSourceResponse(gen())

    # ==============================================================
    # 工作区
    # ==============================================================
    @app.get("/api/workspace")
    def workspace_get() -> dict[str, Any]:
        return get_workspace().snapshot()

    @app.post("/api/workspace/cues")
    def workspace_load_cues(req: dict[str, Any]) -> dict[str, Any]:
        path = req.get("path", "")
        as_translation = bool(req.get("as_translation", False))
        if not path or not Path(path).exists():
            raise HTTPException(status_code=400, detail="字幕文件不存在")
        try:
            cues = get_workspace().load_cues_from_file(path, as_translation)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc))
        return {"count": len(cues), "cues": [c.to_dict() for c in cues]}

    @app.post("/api/workspace/cues/update")
    def workspace_update_cues(req: CuesUpload) -> dict[str, Any]:
        """前端表格编辑后回写。"""
        cues = [Cue.from_dict(d) for d in req.cues]
        ws = get_workspace()
        ws.cues = cues
        ws.updated_at = time.time()
        return {"count": len(cues)}

    @app.delete("/api/workspace")
    def workspace_clear() -> dict[str, Any]:
        get_workspace().clear()
        return {"ok": True}

    # ==============================================================
    # 字幕样式
    # ==============================================================
    @app.get("/api/styles")
    def styles_list() -> dict[str, Any]:
        return {"styles": style_store.list_styles(_cfg())}

    @app.post("/api/styles")
    def styles_save(req: StyleUpdate) -> dict[str, Any]:
        try:
            profile = style_store.save_style(req.model_dump(), _cfg())
        except style_store.StyleError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"style": profile}

    @app.delete("/api/styles")
    def styles_delete(name: str = Query(...)) -> dict[str, Any]:
        """删除样式。用 query 参数而非路径参数，避免样式名中的 / 被编码成 %2F 导致 405。"""
        ok = style_store.delete_style(name, _cfg())
        return {"deleted": ok}

    # ==============================================================
    # 设置
    # ==============================================================
    @app.get("/api/config")
    def config_get() -> dict[str, Any]:
        c = _cfg()
        raw = c.raw
        # 脱敏：api_key 不完整返回
        api_key = c.get("translate.api_key", "")
        return {
            "path": str(c.path),
            "model_root": str(c.model_root),
            "output_dir": str(c.output_dir),
            "values": raw,
            "api_key_masked": (api_key[:4] + "****" + api_key[-2:]) if len(api_key) > 6 else "",
        }

    @app.put("/api/config")
    def config_put(req: ConfigUpdate) -> dict[str, Any]:
        c = _cfg()
        for dotted, value in req.values.items():
            c.set(dotted, value)
        try:
            path = save_config(c)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "path": str(path)}

    # ==============================================================
    # 静态前端（build 产物）
    # ==============================================================
    if WEB_DIST.exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/{full_path:path}")
        def spa(full_path: str):
            target = WEB_DIST / full_path
            if full_path and target.is_file():
                return FileResponse(target)
            return FileResponse(WEB_DIST / "index.html")

    return app


app = create_app()


def main() -> None:
    """命令行启动（供 python -m lvs.api 或脚本调用）。"""
    import uvicorn

    q()  # 确保队列 worker 启动
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
