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


def test_replicate_hosted_minimax_music_models_present_with_lyrics_param(registry):
    """MiniMax closed its direct platform.minimax.io Music API to new
    signups (2026-08-20) - these Replicate-hosted "Official" listings are a
    separate commercial arrangement and unaffected. Both use a real
    prompt+lyrics shape, unlike the removed minimax/music-01 (see
    test_minimax_music_01_removed_as_unusably_reference_audio_based below),
    matching PrismCut's own Audio Lab UI."""
    m15 = registry.find("replicate", "minimax/music-1.5")
    m26 = registry.find("replicate", "minimax/music-2.6")
    ace = registry.find("replicate", "fishaudio/ace-step-1.5")
    assert m15 and "music" in m15.caps
    assert any(p["name"] == "lyrics" for p in m15.params)
    assert m26 and "music" in m26.caps
    assert any(p["name"] == "lyrics" for p in m26.params)
    assert any(p["name"] == "is_instrumental" for p in m26.params)
    assert ace and "music" in ace.caps
    assert {"lyrics", "duration", "bpm"} <= {p["name"] for p in ace.params}


def test_minimax_music_01_removed_as_unusably_reference_audio_based(registry):
    """Real user-reported crash: music-01's actual Replicate schema has no
    free-text style prompt at all - it needs an existing song/voice/
    instrumental audio FILE to imitate, which PrismCut's Audio Lab has no
    UI to supply. Every call through the plain prompt+lyrics UI failed with
    Replicate error E006 ("At least one reference..."). Removed entirely
    rather than left selectable-but-guaranteed-to-fail - confirm it stays
    gone rather than silently reappearing in a future models.json edit."""
    assert registry.find("replicate", "minimax/music-01") is None


def test_user_overlay_roundtrip(registry):
    registry.save_user_model({"id": "my-model", "provider": "custom",
                              "label": "Mine", "caps": ["chat"], "params": []})
    m = registry.find("custom", "my-model")
    assert m and m.user
    registry.remove_user_model("custom", "my-model")
    assert registry.find("custom", "my-model") is None
