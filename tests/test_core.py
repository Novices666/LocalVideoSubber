from __future__ import annotations

from pathlib import Path

from lvs.config import Config, check_llm, resolve_model_path
from lvs.translate.llm_client import LlmClient, LlmConfig
from lvs.media import plan_chunks, safe_stem
from lvs.subtitle.io import parse_srt, write_srt
from lvs.subtitle.models import Cue, Segment
from lvs.subtitle.segmenter import SegmentOptions, segments_to_cues


def test_srt_round_trip(tmp_path: Path) -> None:
    cues = [
        Cue(index=1, start=0.0, end=1.25, text="Hello", translation="你好"),
        Cue(index=2, start=1.5, end=3.0, text="World", translation="世界"),
    ]
    path = write_srt(cues, tmp_path / "roundtrip.srt", mode="both")
    loaded = parse_srt(path.read_text(encoding="utf-8"))

    assert len(loaded) == 2
    assert loaded[0].text == "你好\nHello"
    assert loaded[1].start == 1.5


def test_segmenter_splits_long_text() -> None:
    cues = segments_to_cues(
        [
            # No word timestamps exercises the proportional fallback path.
            Segment(
                start=0.0,
                end=6.0,
                text="This is a long sentence. It should be split into readable subtitle cues.",
            )
        ],
        SegmentOptions(max_chars_latin=24, max_duration=8.0),
    )

    assert len(cues) >= 2
    assert all(c.end > c.start for c in cues)
    assert all(cues[i].end <= cues[i + 1].start + 0.02 for i in range(len(cues) - 1))


def test_media_helpers() -> None:
    assert safe_stem('a<>:"|?*b.mp4') == "a_______b"
    chunks = plan_chunks(1900, threshold=1800, chunk_len=900)
    assert [(c.start, c.end) for c in chunks] == [(0.0, 900.0), (900.0, 1800.0), (1800.0, 1900)]


def test_translation_service_is_optional_when_unconfigured() -> None:
    item = check_llm(Config(raw={"translate": {"base_url": ""}}))
    assert item.ok is False
    assert item.level == "optional"


def test_asr_path_falls_back_to_matching_local_model(tmp_path: Path) -> None:
    root = tmp_path / "models"
    turbo = root / "asr" / "faster-whisper-large-v3-turbo"
    tiny = root / "asr" / "faster-whisper-tiny"
    for model in (turbo, tiny):
        model.mkdir(parents=True)
        for name in ("config.json", "model.bin", "tokenizer.json", "vocabulary.json"):
            (model / name).write_text("ok", encoding="utf-8")

    cfg = Config(raw={
        "model_root": str(root),
        "asr": {"model": "asr/faster-whisper-large-v3"},
    })
    resolved, source = resolve_model_path(cfg, "asr")
    assert resolved.endswith("faster-whisper-large-v3-turbo")
    assert source == "local-fallback"


def test_llm_disable_thinking_adds_chat_template_flag() -> None:
    client = LlmClient(LlmConfig(model="qwen/qwen3.5-9b", disable_thinking=True))
    captured: dict = {}

    def fake_post(body):
        captured.update(body)
        return "{}"

    client._post_chat = fake_post  # type: ignore[method-assign]
    assert client.chat([{"role": "user", "content": "hello"}]) == "{}"
    assert captured["chat_template_kwargs"] == {"enable_thinking": False}
    assert captured["reasoning_effort"] == "none"
    client.close()
