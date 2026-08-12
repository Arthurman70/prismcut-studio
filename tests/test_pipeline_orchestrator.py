import pytest

from prismcut.core.pipeline_orchestrator import _BatchTracker, _parse_breakdown, resolve_video_plan


class _FakeModel:
    def __init__(self, key, caps):
        self._key = key
        self.caps = caps

    @property
    def key(self):
        return self._key


class _FakeRegistry:
    def __init__(self, models: dict):
        self._models = models

    def by_key(self, key):
        return self._models.get(key)


PLAIN_VIDEO = _FakeModel("provA::plain-video", ["video_generate"])
NATIVE_LIPSYNC_VIDEO = _FakeModel("provB::native-lipsync-video", ["video_generate", "lip_sync"])
LIPSYNC_MODEL = _FakeModel("fal::sync-lipsync", ["lip_sync"])

REGISTRY = _FakeRegistry({
    PLAIN_VIDEO.key: PLAIN_VIDEO,
    NATIVE_LIPSYNC_VIDEO.key: NATIVE_LIPSYNC_VIDEO,
    LIPSYNC_MODEL.key: LIPSYNC_MODEL,
})


def test_resolve_video_plan_plain_video_no_lipsync_requested():
    plan = resolve_video_plan(REGISTRY, PLAIN_VIDEO.key, "")
    assert plan.video_model is PLAIN_VIDEO
    assert plan.lipsync_model is None
    assert plan.native_audio is False


def test_resolve_video_plan_explicit_lipsync_model_wins_even_if_video_model_is_plain():
    plan = resolve_video_plan(REGISTRY, PLAIN_VIDEO.key, LIPSYNC_MODEL.key)
    assert plan.video_model is PLAIN_VIDEO
    assert plan.lipsync_model is LIPSYNC_MODEL
    assert plan.native_audio is False


def test_resolve_video_plan_native_audio_branch_when_video_model_itself_has_lip_sync_cap():
    plan = resolve_video_plan(REGISTRY, NATIVE_LIPSYNC_VIDEO.key, "")
    assert plan.video_model is NATIVE_LIPSYNC_VIDEO
    assert plan.lipsync_model is None
    assert plan.native_audio is True


def test_resolve_video_plan_explicit_lipsync_model_takes_priority_over_native_capability():
    # If the caller explicitly picked a separate lip-sync model, use it -
    # even if the video model happens to also claim native lip_sync.
    plan = resolve_video_plan(REGISTRY, NATIVE_LIPSYNC_VIDEO.key, LIPSYNC_MODEL.key)
    assert plan.lipsync_model is LIPSYNC_MODEL
    assert plan.native_audio is False


def test_resolve_video_plan_unknown_video_model_raises():
    with pytest.raises(ValueError):
        resolve_video_plan(REGISTRY, "nope::does-not-exist", "")


def test_resolve_video_plan_unknown_lipsync_model_raises():
    with pytest.raises(ValueError):
        resolve_video_plan(REGISTRY, PLAIN_VIDEO.key, "nope::does-not-exist")


# --------------------------------------------------------------- _BatchTracker
def test_batch_tracker_fires_on_all_done_exactly_once_when_all_succeed():
    fired = []
    t = _BatchTracker(3, lambda: fired.append(1))
    t.one_done(True)
    assert fired == []
    t.one_done(True)
    assert fired == []
    t.one_done(True)
    assert fired == [1]
    assert t.failures == []


def test_batch_tracker_tracks_failures_but_still_fires_once_all_report_in():
    fired = []
    t = _BatchTracker(2, lambda: fired.append(1))
    t.one_done(False, "scene 1 failed")
    t.one_done(True)
    assert fired == [1]
    assert t.failures == ["scene 1 failed"]


def test_batch_tracker_of_zero_scenes_would_need_manual_completion_check():
    # A tracker is only ever constructed with len(targets) > 0 by the
    # orchestrator (the 0-scene case is special-cased before constructing
    # one) - this just documents that a tracker with total=0 doesn't
    # auto-fire on construction, so callers must guard the empty case
    # themselves (which run_image_batch/run_video_batch/run_audio_batch do).
    fired = []
    _BatchTracker(0, lambda: fired.append(1))
    assert fired == []


# ------------------------------------------------------------- _parse_breakdown
def test_parse_breakdown_clean_json():
    text = '[{"script": "A robot wakes up.", "narration": "Where am I?"}, ' \
          '{"script": "It looks around.", "narration": ""}]'
    scenes = _parse_breakdown(text)
    assert len(scenes) == 2
    assert scenes[0].index == 0
    assert scenes[0].script == "A robot wakes up."
    assert scenes[0].narration == "Where am I?"
    assert scenes[1].narration == ""


def test_parse_breakdown_strips_markdown_code_fences():
    text = '```json\n[{"script": "Sunset over water.", "narration": ""}]\n```'
    scenes = _parse_breakdown(text)
    assert len(scenes) == 1
    assert scenes[0].script == "Sunset over water."


def test_parse_breakdown_malformed_json_returns_empty_list():
    assert _parse_breakdown("not json at all") == []
    assert _parse_breakdown("") == []


def test_parse_breakdown_non_list_json_returns_empty_list():
    assert _parse_breakdown('{"script": "just an object, not a list"}') == []


def test_parse_breakdown_tolerates_plain_string_items():
    scenes = _parse_breakdown('["Just a scene description with no narration field"]')
    assert len(scenes) == 1
    assert scenes[0].script == "Just a scene description with no narration field"
    assert scenes[0].narration == ""


def test_parse_breakdown_scene_indices_are_sequential():
    text = '[{"script": "one"}, {"script": "two"}, {"script": "three"}]'
    scenes = _parse_breakdown(text)
    assert [s.index for s in scenes] == [0, 1, 2]
