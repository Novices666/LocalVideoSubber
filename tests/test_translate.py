"""翻译流水线测试（用 mock LLM 服务，不需要真实模型）。

跑法： pytest tests/test_translate.py -q
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from lvs.subtitle.models import Cue
from lvs.translate.llm_client import LlmClient, LlmConfig, extract_json
from lvs.translate.pipeline import SubtitleTranslator, TranslateOptions
from lvs.translate.prompts import build_system_prompt

# --------------------------------------------------------------------------
# Mock LLM 服务
# --------------------------------------------------------------------------
DICT = {
    "Hello everyone": "大家好",
    "welcome to this video": "欢迎来到本期视频",
    "Today we build a tool": "今天我们来做个工具",
    "It handles subtitles": "它处理字幕",
    "Goodbye": "再见",
}

BEHAVIOR = {"mode": "normal"}  # normal | drop | linefmt | chatter | badjson
DROP_IDS: set[int] = set()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静音
        pass

    def do_GET(self):
        if self.path.endswith("/models"):
            body = json.dumps({"data": [{"id": "mock-model"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        user = "\n".join(
            m.get("content", "") for m in req.get("messages", []) if m.get("role") == "user"
        )

        import re

        pairs = [(int(a), b.strip()) for a, b in re.findall(r"^\[(\d+)\]\s*(.+)$", user, re.M)]
        ctx_start = user.find("前文（仅供理解上下文")
        if ctx_start != -1:
            ctx_end = user.find("请翻译以下字幕")
            ctx_block = user[ctx_start:ctx_end] if ctx_end > ctx_start else ""
            ctx_ids = {int(x) for x in re.findall(r"^\[(\d+)\]", ctx_block, re.M)}
            pairs = [(i, t) for i, t in pairs if i not in ctx_ids]

        mode = BEHAVIOR["mode"]
        result = {}
        for idx, text in pairs:
            if mode == "drop" and idx in DROP_IDS:
                continue
            result[str(idx)] = DICT.get(text, f"译:{text}")

        if mode == "linefmt":
            content = "\n".join(f"{k}. {v}" for k, v in result.items())
        elif mode == "chatter":
            content = (
                "好的，我来帮你翻译这些字幕。\n\n```json\n"
                + json.dumps(result, ensure_ascii=False)
                + "\n```\n\n希望对你有帮助！"
            )
        elif mode == "badjson":
            content = "{" + ", ".join(f'"{k}": "{v}"' for k, v in result.items()) + ",}"
        else:
            content = json.dumps(result, ensure_ascii=False)

        body = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": content}}]}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def server():
    srv = HTTPServer(("127.0.0.1", 18080), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()


@pytest.fixture(autouse=True)
def reset_state():
    """每个测试前重置 mock 状态。"""
    BEHAVIOR["mode"] = "normal"
    DROP_IDS.clear()
    yield
    BEHAVIOR["mode"] = "normal"
    DROP_IDS.clear()


def make_cues(texts: list[str]) -> list[Cue]:
    return [
        Cue(index=i, start=i * 2.0, end=i * 2.0 + 1.8, text=t)
        for i, t in enumerate(texts, start=1)
    ]


def translate(texts, **opts):
    cues = make_cues(texts)
    client = LlmClient(
        LlmConfig(base_url="http://127.0.0.1:18080/v1", model="mock-model", max_retries=2)
    )
    t = SubtitleTranslator(client, TranslateOptions(**opts))
    res = t.translate_cues(cues)
    return cues, res


# --------------------------------------------------------------------------
# 测试
# --------------------------------------------------------------------------
def test_basic_batch(server):
    cues, res = translate(
        ["Hello everyone", "welcome to this video", "Today we build a tool"], batch_size=25
    )
    assert res.translated == 3
    assert cues[0].translation == "大家好"
    assert cues[1].translation == "欢迎来到本期视频"


def test_multi_batch_context(server):
    texts = ["Hello everyone", "welcome to this video", "Today we build a tool",
             "It handles subtitles", "Goodbye"]
    cues, res = translate(texts, batch_size=2, context_size=2)
    assert res.translated == 5
    assert res.batches >= 2
    assert cues[-1].translation == "再见"


def test_line_format_reply(server):
    BEHAVIOR["mode"] = "linefmt"
    _, res = translate(["Hello everyone", "Goodbye"], batch_size=10)
    assert res.translated == 2


def test_chatter_reply(server):
    BEHAVIOR["mode"] = "chatter"
    _, res = translate(["Hello everyone", "Goodbye"], batch_size=10)
    assert res.translated == 2


def test_bad_json_trailing_comma(server):
    BEHAVIOR["mode"] = "badjson"
    _, res = translate(["Hello everyone", "Goodbye"], batch_size=10)
    assert res.translated == 2


def test_drop_retry(server):
    BEHAVIOR["mode"] = "drop"
    drop_state = {"first": True}
    orig = Handler.do_POST

    def flaky(self):
        if drop_state["first"]:
            DROP_IDS.update({2, 4})
            drop_state["first"] = False
        else:
            DROP_IDS.clear()
        orig(self)

    Handler.do_POST = flaky
    try:
        _, res = translate(
            ["Hello everyone", "welcome to this video", "Today we build a tool",
             "It handles subtitles", "Goodbye"],
            batch_size=10, max_batch_retries=3,
        )
        assert res.translated == 5
        assert res.retries >= 1
    finally:
        Handler.do_POST = orig


def test_glossary_injection(server):
    sp = build_system_prompt("en", "zh", {"Jutsu": "忍术", "Naruto": "鸣人"})
    assert "忍术" in sp and "鸣人" in sp
    assert "字幕" in sp


def test_concurrency(server):
    _, res = translate(
        ["Hello everyone", "welcome to this video", "Today we build a tool",
         "It handles subtitles", "Goodbye"],
        batch_size=2, concurrency=3,
    )
    assert res.translated == 5


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"1": "a"}', {"1": "a"}),
        ('```json\n{"1": "a"}\n```', {"1": "a"}),
        ('前言\n{"1": "a"}\n后记', {"1": "a"}),
        ('[{"index":1,"translation":"a"}]', [{"index": 1, "translation": "a"}]),
        ('{"1": "a",}', {"1": "a"}),
    ],
)
def test_extract_json_boundaries(raw, expected):
    assert extract_json(raw) == expected


def test_connection_error_hint(server):
    client = LlmClient(LlmConfig(base_url="http://127.0.0.1:19999/v1", model="x"))
    with pytest.raises(Exception) as exc_info:
        client.chat([{"role": "user", "content": "hi"}])
    msg = str(exc_info.value)
    assert "服务未启动" in msg or "llama-server" in msg
