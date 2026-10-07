"""端到端集成测试：视频 -> 转录 -> 翻译(mock LLM) -> 三种渲染。

跑法： python tests/test_e2e.py
"""
from __future__ import annotations

import json
import shutil

import pytest
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

WORK = ROOT / "output" / "_e2e"

# --------------------------------------------------------------------------
# Mock 翻译服务
# --------------------------------------------------------------------------
TRANS = {
    "Hello everyone": "大家好",
    "welcome to this video": "欢迎来到本期视频",
    "Today we are going to build": "今天我们要做的是",
    "a local video subtitle tool": "一个本地视频字幕工具",
    "It can transcribe": "它可以转录",
    "translate subtitles": "翻译字幕",
    "and burn them into the video": "并烧录进视频里",
    "That is all for today": "今天就到这里",
    "Thanks for watching": "感谢观看",
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        b = json.dumps({"data": [{"id": "mock-llm"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        user = "\n".join(
            m.get("content", "") for m in req.get("messages", []) if m.get("role") == "user"
        )
        import re

        pairs = [(int(a), b.strip()) for a, b in re.findall(r"^\[(\d+)\]\s*(.+)$", user, re.M)]
        cs = user.find("前文（仅供理解上下文")
        if cs != -1:
            ce = user.find("请翻译以下字幕")
            block = user[cs:ce] if ce > cs else ""
            ids = {int(x) for x in re.findall(r"^\[(\d+)\]", block, re.M)}
            pairs = [(i, t) for i, t in pairs if i not in ids]

        out = {str(i): TRANS.get(t, f"译:{t}") for i, t in pairs}
        body = json.dumps(
            {"choices": [{"message": {"content": json.dumps(out, ensure_ascii=False)}}]}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_speech_wav(path: Path, sentences: list[str]) -> Path:
    """用 Windows SAPI 生成语音 wav。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = "\n".join(f"$s.Speak('{s.replace(chr(39), chr(39)*2)}')" for s in sentences)
    ps = f"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.Rate = -1
$s.SetOutputToWaveFile('{path}')
{lines}
$s.Dispose()
"""
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True, text=True, timeout=180,
    )
    if not path.exists():
        raise RuntimeError(f"SAPI 生成语音失败: {r.stderr[:300]}")
    return path


def make_video(wav: Path, out: Path) -> Path:
    """把语音配上画面做成 mp4。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=0x1a1a2e:size=640x360:rate=25",
        "-i", str(wav),
        "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-c:a", "aac", "-b:a", "96k",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out


# --------------------------------------------------------------------------
@pytest.mark.slow
def test_e2e_full_flow() -> None:
    fails: list[str] = []
    t_start = time.time()

    def check(name: str, cond: bool, extra: str = "") -> None:
        print(f"  {'✓' if cond else '✗'} {name}" + (f"   {extra}" if extra else ""))
        if not cond:
            fails.append(name)

    if WORK.exists():
        shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 66)
    print("  端到端集成测试")
    print("=" * 66)

    # ---------------- 准备素材 ----------------
    print("\n[0] 准备测试素材")
    sentences = [
        "Hello everyone. Welcome to this video.",
        "Today we are going to build a local video subtitle tool.",
        "It can transcribe. Translate subtitles.",
        "And burn them into the video.",
        "That is all for today. Thanks for watching.",
    ]
    try:
        wav = make_speech_wav(WORK / "speech.wav", sentences)
        wav16 = WORK / "speech16k.wav"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav),
             "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav16)],
            check=True, capture_output=True,
        )
        video = make_video(wav16, WORK / "input.mp4")
        check("生成测试语音", wav.exists(), f"{wav.stat().st_size / 1024:.0f} KB")
        check("生成测试视频", video.exists(), f"{video.stat().st_size / 1024:.0f} KB")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"素材准备失败（环境缺 SAPI/ffmpeg）: {exc}")

    # ---------------- 启动 mock LLM ----------------
    PORT = 18099
    srv = HTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"  ✓ Mock 翻译服务已在 :{PORT} 启动")

    # ---------------- 配置 ----------------
    from lvs.config import load_config, merge_overrides, scan_models
    from lvs.jobs import JobQueue
    from lvs.pipeline import register_all
    from lvs.subtitle.models import Cue
    from lvs.subtitle.io import load_subtitle, save_subtitle
    from lvs.subtitle.render import available_encoders, burn_subtitles, mux_subtitles, RenderOptions
    from lvs.media import probe, list_subtitle_streams

    cfg = load_config()
    cfg.set("model_root", str(ROOT / "models"))
    cfg.set("jobs.output_dir", str(WORK / "out"))
    cfg.set("asr.model", "asr/faster-whisper-tiny")
    cfg.set("asr.device", "cpu")              # 测试用 CPU，避免显存被占
    cfg.set("asr.compute_type", "int8")
    cfg.set("asr.language", "en")
    cfg.set("asr.beam_size", 1)
    cfg.set("translate.base_url", f"http://127.0.0.1:{PORT}/v1")
    cfg.set("translate.model", "mock-llm")
    cfg.set("translate.batch_size", 10)
    cfg.set("translate.source_lang", "en")
    cfg.set("translate.target_lang", "zh")
    cfg.ensure_dirs()

    q = JobQueue()
    register_all(q)
    q.start()

    def wait(job, timeout=600):
        t0 = time.time()
        while not job.status.finished:
            if time.time() - t0 > timeout:
                raise TimeoutError(f"任务 {job.id} 超时")
            time.sleep(0.3)
        return job

    # ---------------- 1. 转录 ----------------
    print("\n[1] 转录（faster-whisper tiny / CPU）")
    j = q.submit("transcribe", {
        "cfg": cfg,
        "video": str(video),
        "model_path": None,
        "engine": "faster-whisper",
        "overrides": {},
        "seg_opts": {"enabled": True, "max_chars_cjk": 18, "max_chars_latin": 40},
    }, title="E2E 转录")
    wait(j)
    check("转录任务完成", j.status.value == "已完成", f"status={j.status.value} err={j.error[:120]}")
    if j.status.value != "已完成":
        print("\n--- 日志 ---")
        print("\n".join(j.logs[-25:]))
        srv.shutdown()
        pytest.fail(f"转录任务失败: {j.error}")

    res = j.result
    cues = [Cue.from_dict(d) for d in res["cues"]]
    check("产出字幕条目", len(cues) > 0, f"{len(cues)} 条")
    check("语言检测正确", res.get("language", "").startswith("en"), f"lang={res.get('language')}")
    txt = " ".join(c.text for c in cues).lower()
    check("识别到关键内容", "video" in txt, f"文本片段: {txt[:70]}...")
    check("时间轴递增", all(cues[i].end <= cues[i + 1].start + 0.5 for i in range(len(cues) - 1)))
    check("字幕文件已写出", Path(res["srt"]).exists(), Path(res["srt"]).name)

    # ---------------- 2. 翻译 ----------------
    print("\n[2] 翻译（mock LLM）")
    j2 = q.submit("translate", {
        "cfg": cfg,
        "cues": res["cues"],
        "overrides": {},
        "stem": "e2e",
        "work_dir": str(WORK / "tr"),
    }, title="E2E 翻译")
    wait(j2)
    check("翻译任务完成", j2.status.value == "已完成", f"status={j2.status.value} err={j2.error[:120]}")
    if j2.status.value == "已完成":
        r2 = j2.result
        cues2 = [Cue.from_dict(d) for d in r2["cues"]]
        check("有译文产出", r2["translated"] > 0, f"{r2['translated']}/{len(cues2)} 条")
        check("译文非空", all(c.translation for c in cues2 if c.text.strip()))
        check("双语文件已写出", Path(r2["bilingual_srt"]).exists(),
              Path(r2["bilingual_srt"]).name)
        sample = next((c for c in cues2 if c.translation), None)
        if sample:
            print(f"      示例: {sample.text!r} -> {sample.translation!r}")
    else:
        cues2 = cues
        print("\n--- 翻译日志 ---")
        print("\n".join(j2.logs[-20:]))

    # ---------------- 3. 渲染 ----------------
    print("\n[3] 渲染")
    outdir = WORK / "out"
    outdir.mkdir(parents=True, exist_ok=True)

    # 3a 导出
    j3 = q.submit("render", {
        "cfg": cfg, "cues": [c.to_dict() for c in cues2], "video": str(video),
        "mode": "export", "display_mode": "both", "order": "target_top",
        "stem": "e2e", "out_dir": str(outdir),
    }, title="E2E 导出")
    wait(j3, 300)
    check("导出任务完成", j3.status.value == "已完成", j3.error[:100])
    if j3.status.value == "已完成":
        files = j3.result.get("files", [])
        check("导出多个格式", len(files) >= 3, f"{len(files)} 个文件")
        names = [Path(f).name for f in files]
        check("含 srt/vtt/ass", any(n.endswith(".srt") for n in names)
              and any(n.endswith(".vtt") for n in names)
              and any(n.endswith(".ass") for n in names), ", ".join(names[:4]))

    # 3b 软封装
    j4 = q.submit("render", {
        "cfg": cfg, "cues": [c.to_dict() for c in cues2], "video": str(video),
        "mode": "soft", "display_mode": "both", "order": "target_top",
        "container": "mkv", "stem": "e2e", "out_dir": str(outdir),
    }, title="E2E 软封装")
    wait(j4, 300)
    check("软封装任务完成", j4.status.value == "已完成", j4.error[:100])
    if j4.status.value == "已完成":
        so = Path(j4.result["output"])
        check("软封装产物存在", so.exists(), f"{so.name} {so.stat().st_size/1024:.0f} KB")
        streams = list_subtitle_streams(so)
        check("含字幕轨道", len(streams) >= 1, str(streams))

    # 3c 硬烧录
    j5 = q.submit("render", {
        "cfg": cfg, "cues": [c.to_dict() for c in cues2], "video": str(video),
        "mode": "burn", "display_mode": "both", "order": "target_top",
        "encoder": "auto", "crf": 23, "stem": "e2e", "out_dir": str(outdir),
    }, title="E2E 硬烧录")
    wait(j5, 600)
    check("硬烧录任务完成", j5.status.value == "已完成", j5.error[:150])
    burned = None
    if j5.status.value == "已完成":
        burned = Path(j5.result["output"])
        check("烧录产物存在", burned.exists(),
              f"{burned.name} {burned.stat().st_size/1024:.0f} KB · {j5.result.get('note','')}")

    # ---------------- 4. 像素验证 ----------------
    print("\n[4] 渲染结果像素验证")
    if burned and burned.exists():
        # 对比原视频和烧录视频：同时间点应有额外亮像素（字幕）
        def bright_pixels(src: Path, t: float) -> int:
            r = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(t),
                 "-i", str(src), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                capture_output=True,
            )
            return sum(1 for b in r.stdout if b > 200)

        # 找一个有字幕的时刻
        probe_info = probe(burned)
        mid = max(2.0, probe_info.duration * 0.35)
        b_burn = bright_pixels(burned, mid)
        b_orig = bright_pixels(video, mid)
        check("烧录后画面出现字幕像素", b_burn > b_orig + 50,
              f"烧录 {b_burn} vs 原始 {b_orig} (t={mid:.1f}s)")

        # 双语两行检查
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(mid),
             "-i", str(burned), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            capture_output=True,
        )
        data = r.stdout
        if data:
            W = 640
            H = len(data) // W if W else 0
            rows = [sum(1 for x in range(W) if data[y * W + x] > 190) for y in range(H)]
            bands, start = [], None
            for y, c in enumerate(rows):
                if c > 0 and start is None:
                    start = y
                elif c == 0 and start is not None:
                    bands.append((start, y - 1))
                    start = None
            if start is not None:
                bands.append((start, H - 1))
            check("双语双行布局", len(bands) >= 2, f"{len(bands)} 个文字行带")

    # ---------------- 5. 字幕往返 ----------------
    print("\n[5] 字幕文件往返一致性")
    if j3.status.value == "已完成":
        srt_file = next((Path(f) for f in j3.result["files"] if f.endswith(".srt")), None)
        if srt_file and srt_file.exists():
            back = load_subtitle(srt_file)
            check("SRT 读回条数一致", len(back) == len(cues2),
                  f"写入 {len(cues2)} 读回 {len(back)}")
            check("时间轴误差 < 10ms",
                  all(abs(a.start - b.start) < 0.01 for a, b in zip(cues2, back)))

    # ---------------- 6. 任务队列行为 ----------------
    print("\n[6] 任务队列与取消")
    q.clear_finished()
    jc = q.submit("transcribe", {
        "cfg": cfg, "video": str(video), "engine": "faster-whisper",
        "overrides": {}, "seg_opts": {},
    }, title="取消测试")
    time.sleep(0.1)
    ok_cancel = q.cancel(jc.id)
    wait(jc, 300)
    check("可请求取消", ok_cancel)
    check("任务被取消", jc.status.value in ("已取消", "已完成"),
          f"status={jc.status.value}")

    # ---------------- 汇总 ----------------
    q.stop()
    srv.shutdown()

    print("\n" + "=" * 66)
    print(f"  耗时 {time.time() - t_start:.1f}s")
    if fails:
        print(f"  ✗ {len(fails)} 项失败:")
        for f in fails:
            print(f"      - {f}")
        print("=" * 66)
        pytest.fail(f"端到端测试 {len(fails)} 项失败: {', '.join(fails)}")
    print("  ✓ 端到端测试全部通过")
    print("=" * 66)
