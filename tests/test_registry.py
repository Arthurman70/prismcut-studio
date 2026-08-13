import json
from pathlib import Path

import pytest

from prismcut.core.registry import BUILTIN_PATH, CAPS, Registry


@pytest.fixture()
def registry(tmp_path):
    return Registry(user_path=tmp_path / "user.json")


def test_builtin_json_is_valid():
    data = json.loads(Path(BUILTIN_PATH).read_text(encoding="utf-8"))
    assert data["providers"] and data["models"]


def test_every_model_has_known_provider_and_caps(registry):
    for m in registry.models:
        assert m.provider in registry.providers, m.id
        assert m.caps, m.id
        for c in m.caps:
            assert c in CAPS, f"{m.id}: unknown cap {c}"


def test_param_schemas_wellformed(registry):
    for m in registry.models:
        for p in m.params:
            assert "name" in p and "type" in p, m.id
            assert p["type"] in ("int", "float", "choice", "bool", "text"), m.id
            if p["type"] == "choice":
                assert p.get("choices"), m.id


def test_capability_queries(registry):
    assert registry.models_with("chat")
    assert registry.models_with("image_generate")
    assert registry.models_with("image_edit")
    assert registry.models_with("video_generate")
    assert registry.models_with("tts")
    assert registry.models_with("music")
    assert registry.models_with("transcribe")


def test_headline_models_present(registry):
    assert registry.find("google", "gemini-3.1-flash-image"), "Nano Banana 2 missing"
    assert registry.find("openai", "sora-2")
    assert registry.find("minimax", "MiniMax-H3"), "Hailuo 03 missing"
    assert registry.find("seedance", "dreamina-seedance-2-0-260128")
    assert registry.find("xai", "grok-imagine-video-1.5")
    assert registry.find("deepseek", "deepseek-v4-flash")


def test_minimax_image_01_present_and_priced(registry):
    m = registry.find("minimax", "image-01")
    assert m and "image_generate" in m.caps
    assert m.key == "minimax::image-01"

    from prismcut.core import pricing
    prices = pricing.load_bundled()["prices"]
    assert prices[m.key]["unit"] == "per_image"


def test_kling_text_and_image_to_video_present(registry):
    t2v = registry.find("fal", "fal-ai/kling-video/v2.6/pro/text-to-video")
    i2v = registry.find("fal", "fal-ai/kling-video/v2.6/pro/image-to-video")
    assert t2v and "video_generate" in t2v.caps
    assert i2v and "image_to_video" in i2v.caps and "video_generate" in i2v.caps
    assert any(p["name"] == "start_image_url" for p in i2v.params)


def test_google_image_models_flagged_with_strict_ip_policy(registry):
    for model_id in ("gemini-3.1-flash-image", "gemini-3-pro-image", "gemini-3.1-flash-lite-image"):
        m = registry.find("google", model_id)
        assert m and m.strict_ip_policy, model_id

    # Not blanket-applied to every model - a plain chat/video model should
    # default to False, same as before this field existed.
    assert registry.find("google", "gemini-3.6-flash").strict_ip_policy is False
    assert registry.find("xai", "grok-imagine-video-1.5").strict_ip_policy is False


def test_user_overlay_roundtrip(registry):
    registry.save_user_model({"id": "my-model", "provider": "custom",
                              "label": "Mine", "caps": ["chat"], "params": []})
    m = registry.find("custom", "my-model")
    assert m and m.user
    registry.remove_user_model("custom", "my-model")
    assert registry.find("custom", "my-model") is None
