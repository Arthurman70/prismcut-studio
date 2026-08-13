import pytest

from prismcut.core.pipeline_orchestrator import (_BatchTracker, _parse_breakdown,
                                                 _seed_scene_durations, resolve_video_plan)


class _FakeModel:
    def __init__(self, key, caps, params=None):
        self._key = key
        self.caps = caps
        self.params = params or []

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


def test_batch_tracker_one_done_is_a_noop_once_already_complete():
    """Bug fix: the Jobs panel's 'Retry' resubmits a failed job with its
    ORIGINAL on_done/on_fail closures, which still close over this same
    tracker - if the batch already finished (this failure was the last one
    remaining, on_all_done() already fired), a later manual retry of that
    one job must not drive remaining negative and re-trigger
    batch-completion a second time."""
    fired = []
    t = _BatchTracker(2, lambda: fired.append(1))
    t.one_done(False, "scene 1 failed")
    t.one_done(False, "scene 2 failed")   # batch complete, on_all_done() fires once
    assert fired == [1]
    assert t.failures == ["scene 1 failed", "scene 2 failed"]

    t.one_done(True)   # a stale retry of scene 2, arriving after completion
    assert fired == [1]              # on_all_done() did NOT fire again
    assert t.remaining == 0          # did not go negative
    assert t.failures == ["scene 1 failed", "scene 2 failed"]   # untouched


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


# --- real-world messy LLM output shapes (the actual bug this session fixes) ---

def test_parse_breakdown_tolerates_preamble_and_fenced_json():
    text = ('Sure! Here\'s the scene breakdown for your movie:\n\n'
           '```json\n[{"script": "A robot wakes up.", "narration": ""}]\n```\n\n'
           'Let me know if you would like any changes!')
    scenes = _parse_breakdown(text)
    assert len(scenes) == 1
    assert scenes[0].script == "A robot wakes up."


def test_parse_breakdown_tolerates_bare_array_with_surrounding_prose_no_fence():
    text = ('Here is the breakdown:\n'
           '[{"script": "Sunset over water.", "narration": ""}, '
           '{"script": "A boat drifts by.", "narration": ""}]\n'
           'Hope that works for you.')
    scenes = _parse_breakdown(text)
    assert len(scenes) == 2
    assert scenes[1].script == "A boat drifts by."


def test_parse_breakdown_skips_a_false_positive_bracket_before_the_real_array():
    # Prose containing a stray '[' (e.g. a citation-style aside) must not be
    # mistaken for the start of the JSON array - the parser should keep
    # trying subsequent '[' occurrences until one actually parses.
    text = ('This brief evokes [in a sense] a classic three-act structure. '
           '[{"script": "Act one begins.", "narration": ""}]')
    scenes = _parse_breakdown(text)
    assert len(scenes) == 1
    assert scenes[0].script == "Act one begins."


def test_parse_breakdown_still_fails_cleanly_on_a_pure_refusal():
    text = "I'm not able to help write a breakdown for that request."
    assert _parse_breakdown(text) == []


# --------------------------------------------------- _seed_scene_durations
def test_seed_scene_durations_noop_when_seconds_is_zero_or_model_is_none():
    from prismcut.core.pipeline import new_scene

    model = _FakeModel("test::video", ["video_generate"],
                       [{"name": "duration", "type": "int", "min": 4, "max": 8, "default": 8}])
    s1 = new_scene(0)
    _seed_scene_durations([s1], 0.0, model)
    assert s1.video_params == {}

    s2 = new_scene(0)
    _seed_scene_durations([s2], 10.0, None)
    assert s2.video_params == {}


def test_seed_scene_durations_noop_without_a_duration_param():
    from prismcut.core.pipeline import new_scene

    model = _FakeModel("test::video", ["video_generate"],
                       [{"name": "aspect_ratio", "type": "choice", "choices": ["16:9"],
                        "default": "16:9"}])
    s = new_scene(0)
    _seed_scene_durations([s], 10.0, model)
    assert s.video_params == {}


def test_seed_scene_durations_clamps_int_range_to_the_models_own_limits():
    from prismcut.core.pipeline import new_scene

    model = _FakeModel("test::video", ["video_generate"],
                       [{"name": "duration", "type": "int", "min": 4, "max": 8, "default": 8}])
    s1, s2 = new_scene(0), new_scene(1)
    _seed_scene_durations([s1, s2], 20.0, model)   # requested well above the model's max
    assert s1.video_params["duration"] == 8
    assert s2.video_params["duration"] == 8

    s3 = new_scene(0)
    _seed_scene_durations([s3], 1.0, model)   # requested well below the model's min
    assert s3.video_params["duration"] == 4


def test_seed_scene_durations_picks_the_closest_choice():
    from prismcut.core.pipeline import new_scene

    model = _FakeModel("test::video", ["video_generate"],
                       [{"name": "duration", "type": "choice", "choices": ["5", "10"],
                        "default": "5"}])
    near_five = new_scene(0)
    _seed_scene_durations([near_five], 7.0, model)
    assert near_five.video_params["duration"] == "5"

    near_ten = new_scene(0)
    _seed_scene_durations([near_ten], 9.0, model)
    assert near_ten.video_params["duration"] == "10"
