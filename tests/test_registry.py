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
    assert registry.find("deepseek", "deepseek-chat")


def test_user_overlay_roundtrip(registry):
    registry.save_user_model({"id": "my-model", "provider": "custom",
                              "label": "Mine", "caps": ["chat"], "params": []})
    m = registry.find("custom", "my-model")
    assert m and m.user
    registry.remove_user_model("custom", "my-model")
    assert registry.find("custom", "my-model") is None
