"""Offline checks that adapters build correct request payloads (no network)."""
import base64

import pytest

import prismcut.core.http as http
from prismcut.core.registry import Registry
from prismcut.providers import make_adapter
from prismcut.providers.base import ChatMessage


class FakeKeychain:
    def __init__(self):
        self.keys = {n: f"sk-test-{n}" for n in
                     ("google", "openai", "anthropic", "xai", "deepseek", "minimax",
                      "seedance", "stability", "bfl", "fal", "replicate",
                      "elevenlabs", "suno", "custom")}

    def get(self, key, default=None):
        return default

    def get_key(self, provider, env=""):
        return self.keys.get(provider)


@pytest.fixture()
def registry(tmp_path):
    return Registry(user_path=tmp_path / "user.json")


@pytest.fixture()
def keychain():
    return FakeKeychain()


def test_google_chat_payload(monkeypatch, registry, keychain, tmp_path):
    captured = {}
    img = tmp_path / "x.png"
    img.write_bytes(b"png-bytes")

    def fake_request_json(method, url, **kw):
        captured.update(method=method, url=url, **kw)
        return {"candidates": [{"content": {"parts": [{"text": "hello"}]}}]}

    monkeypatch.setattr(http, "request_json", fake_request_json)
    adapter = make_adapter("google", keychain, registry)
    out = adapter.chat("gemini-3.6-flash", [ChatMessage("user", "hi", [str(img)])])
    assert out == "hello"
    assert "models/gemini-3.6-flash:generateContent" in captured["url"]
    parts = captured["json_body"]["contents"][0]["parts"]
    assert parts[0]["text"] == "hi"
    assert parts[1]["inline_data"]["data"] == base64.b64encode(b"png-bytes").decode()
    assert captured["headers"]["x-goog-api-key"].startswith("sk-test")


def test_nano_banana_edit_includes_mask_instruction(monkeypatch, registry, keychain, tmp_path):
    captured = {}
    img = tmp_path / "img.png"
    img.write_bytes(b"i")
    mask = tmp_path / "mask.png"
    mask.write_bytes(b"m")
    fake_png = base64.b64encode(b"result").decode()

    def fake_request_json(method, url, **kw):
        captured.update(url=url, **kw)
        return {"candidates": [{"content": {"parts": [
            {"inline_data": {"data": fake_png}}]}}]}

    monkeypatch.setattr(http, "request_json", fake_request_json)
    adapter = make_adapter("google", keychain, registry)
    outs = adapter.edit_image("gemini-3.1-flash-image", "remove the car",
                              str(img), str(mask))
    assert len(outs) == 1
    parts = captured["json_body"]["contents"][0]["parts"]
    assert "mask" in parts[0]["text"].lower()
    assert len(parts) == 3
    assert captured["json_body"]["generationConfig"]["responseModalities"] == ["IMAGE"]


def test_openai_chat_is_openai_compatible(monkeypatch, registry, keychain):
    captured = {}

    def fake_request_json(method, url, **kw):
        captured.update(url=url, **kw)
        return {"choices": [{"message": {"content": "yo"}}]}

    monkeypatch.setattr(http, "request_json", fake_request_json)
    adapter = make_adapter("openai", keychain, registry)
    out = adapter.chat("gpt-5.6-terra", [ChatMessage("user", "hey")])
    assert out == "yo"
    assert captured["url"].endswith("/v1/chat/completions")
    assert captured["json_body"]["model"] == "gpt-5.6-terra"
    assert captured["headers"]["Authorization"].startswith("Bearer sk-test")


def test_minimax_video_flow(monkeypatch, registry, keychain, tmp_path):
    calls = []

    def fake_request_json(method, url, **kw):
        calls.append(url)
        if "video_generation" in url and method == "POST":
            return {"task_id": "t1", "base_resp": {"status_code": 0}}
        if "query/video_generation" in url:
            return {"status": "Success", "file_id": "f1",
                    "base_resp": {"status_code": 0}}
        if "files/retrieve" in url:
            return {"file": {"download_url": "https://cdn.example/video.mp4"},
                    "base_resp": {"status_code": 0}}
        raise AssertionError(url)

    def fake_download(url, dest, **kw):
        from pathlib import Path
        Path(dest).write_bytes(b"mp4")
        return dest

    monkeypatch.setattr(http, "request_json", fake_request_json)
    monkeypatch.setattr(http, "download", fake_download)
    adapter = make_adapter("minimax", keychain, registry)
    out = adapter.generate_video("MiniMax-H3", "a cat surfing",
                                 {"duration": 6, "resolution": "2K"})
    assert out.endswith(".mp4")
    assert any("video_generation" in c for c in calls)


def test_seedance_appends_flags(monkeypatch, registry, keychain):
    captured = {}

    def fake_request_json(method, url, **kw):
        if method == "POST":
            captured.update(**kw)
            return {"id": "task9"}
        return {"status": "succeeded", "content": {"video_url": "https://x/video.mp4"}}

    monkeypatch.setattr(http, "request_json", fake_request_json)
    monkeypatch.setattr(http, "download", lambda url, dest, **kw: dest)
    adapter = make_adapter("seedance", keychain, registry)
    adapter.generate_video("dreamina-seedance-2-0-260128", "sunset drone shot",
                           {"resolution": "720p", "duration": 5, "aspect_ratio": "16:9"})
    text = captured["json_body"]["content"][0]["text"]
    assert "--resolution 720p" in text and "--duration 5" in text and "--ratio 16:9" in text


def test_fal_kling_image_to_video_uses_start_image_url_field(monkeypatch, registry, keychain,
                                                              tmp_path):
    """Kling's fal.ai image-to-video endpoint names its source-image field
    "start_image_url", unlike every other fal video model here which uses
    the generic "image_url" - FalAI._payload() must route to whichever
    field the model's own registry params declare (verified against fal's
    real API docs), not blindly use the generic name."""
    captured = {}
    img = tmp_path / "frame.png"
    img.write_bytes(b"png-bytes")

    def fake_request_json(method, url, **kw):
        captured.update(method=method, url=url, **kw)
        return {"video": {"url": "https://cdn.example/kling-out.mp4"}}

    monkeypatch.setattr(http, "request_json", fake_request_json)
    monkeypatch.setattr(http, "download", lambda url, dest, **kw: dest)
    adapter = make_adapter("fal", keychain, registry)
    model = registry.by_key("fal::fal-ai/kling-video/v2.6/pro/image-to-video")
    out = adapter.generate_video(model.id, "a dragon flying over mountains",
                                 model.default_params(), image=str(img))
    assert out.endswith(".mp4")
    assert "fal-ai/kling-video/v2.6/pro/image-to-video" in captured["url"]
    body = captured["json_body"]
    assert "start_image_url" in body
    assert body["start_image_url"].startswith("data:")
    assert "image_url" not in body


def test_fal_hailuo_image_to_video_still_uses_generic_image_url_field(monkeypatch, registry,
                                                                       keychain, tmp_path):
    """Regression guard for the fix above: every OTHER fal video model
    (no start_image_url declared in its params) must keep using the plain
    "image_url" field it always has."""
    captured = {}
    img = tmp_path / "frame.png"
    img.write_bytes(b"png-bytes")

    def fake_request_json(method, url, **kw):
        captured.update(method=method, url=url, **kw)
        return {"video": {"url": "https://cdn.example/hailuo-out.mp4"}}

    monkeypatch.setattr(http, "request_json", fake_request_json)
    monkeypatch.setattr(http, "download", lambda url, dest, **kw: dest)
    adapter = make_adapter("fal", keychain, registry)
    model = registry.by_key("fal::fal-ai/minimax/hailuo-02/pro/image-to-video")
    adapter.generate_video(model.id, "a cat surfing", model.default_params(), image=str(img))
    body = captured["json_body"]
    assert "image_url" in body
    assert "start_image_url" not in body


def test_every_provider_instantiates(registry, keychain):
    for name in registry.providers:
        adapter = make_adapter(name, keychain, registry)
        assert adapter.name == name
