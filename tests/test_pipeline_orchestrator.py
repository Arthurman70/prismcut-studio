import pytest

from prismcut.core.pipeline_orchestrator import (_BatchTracker, _augment_prompt_for_model,
                                                 _dispatch_window, _is_moderation_failure,
                                                 _parse_breakdown, _seed_scene_durations,
                                                 resolve_video_plan)


class _FakeModel:
    def __init__(self, key, caps, params=None, strict_ip_policy=False):
        self._key = key
        self.caps = caps
        self.params = params or []
        self.strict_ip_policy = strict_ip_policy

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


# ------------------------------------------------------------- _dispatch_window
def test_dispatch_window_concurrency_1_dispatches_one_at_a_time():
    dispatched = []

    def start_one(item, on_settled):
        dispatched.append(item)

    _dispatch_window([1, 2, 3], 1, start_one)
    assert dispatched == [1]   # only the first item primed, nothing settled yet


def test_dispatch_window_primes_up_to_concurrency_items_up_front():
    dispatched = []
    _dispatch_window([1, 2, 3, 4, 5], 3, lambda item, on_settled: dispatched.append(item))
    assert dispatched == [1, 2, 3]   # primed 3 at once; none have settled


def test_dispatch_window_refills_one_at_a_time_as_each_settles():
    dispatched = []
    on_settled_ref = []

    def start_one(item, on_settled):
        dispatched.append(item)
        on_settled_ref.append(on_settled)

    _dispatch_window([1, 2, 3, 4, 5], 2, start_one)
    assert dispatched == [1, 2]
    settle = on_settled_ref[0]   # every item is handed the SAME shared callback
    settle()   # simulate item 1 finishing
    assert dispatched == [1, 2, 3]   # window refilled immediately with the next queued item
    settle()   # item 2 (or 3 - doesn't matter, it's one shared window) finishing
    assert dispatched == [1, 2, 3, 4]
    settle()
    assert dispatched == [1, 2, 3, 4, 5]
    settle()
    settle()
    assert dispatched == [1, 2, 3, 4, 5]   # fully drained, extra settles are harmless


def test_dispatch_window_concurrency_higher_than_queue_length_dispatches_everything_once():
    dispatched = []
    _dispatch_window([1, 2], 5, lambda item, on_settled: dispatched.append(item))
    assert dispatched == [1, 2]


def test_dispatch_window_empty_queue_is_a_noop():
    calls = []
    _dispatch_window([], 3, lambda item, on_settled: calls.append(item))
    assert calls == []


def test_dispatch_window_concurrency_zero_or_negative_still_dispatches_at_least_one():
    # Defensive floor - a caller passing 0/negative concurrency (e.g. a
    # corrupted saved-pipeline field) must not silently dispatch nothing
    # and hang the batch forever.
    dispatched = []
    _dispatch_window([1, 2], 0, lambda item, on_settled: dispatched.append(item))
    assert dispatched == [1]
    dispatched2 = []
    _dispatch_window([1, 2], -3, lambda item, on_settled: dispatched2.append(item))
    assert dispatched2 == [1]


def test_dispatch_window_every_item_shares_the_same_on_settled_callback():
    # Not a chain of per-item closures over narrowing list-slices - one
    # shared dispatcher function, handed out identically to every item.
    # This is what makes the next test's "stale settle after drained is a
    # no-op" property hold for ANY item's completion, not just the last one.
    seen = []
    _dispatch_window([1, 2, 3], 3, lambda item, on_settled: seen.append(on_settled))
    assert seen[0] is seen[1] is seen[2]


def test_dispatch_window_a_stale_settle_callback_after_the_queue_is_drained_is_a_noop():
    """The concrete bug this shared-queue design fixes: the Jobs panel's
    'Retry' resubmits a failed job with its ORIGINAL on_settled closure. If
    that closure fires again long after the whole batch has already
    drained (a stale retry finally succeeding after the fact), it must not
    re-dispatch anything - a narrowing-list-slice recursion (the design
    this replaced) would instead re-walk and re-dispatch everything that
    originally came after the retried item."""
    dispatched = []
    on_settled_ref = []

    def start_one(item, on_settled):
        dispatched.append(item)
        on_settled_ref.append(on_settled)

    _dispatch_window([1, 2], 1, start_one)
    stale = on_settled_ref[0]   # captured early, as a real retry callback would be
    stale()   # item 1 settles "for real" - dispatches item 2
    stale()   # item 2 settles "for real" - queue now empty
    assert dispatched == [1, 2]

    stale()   # a stale retry of item 1, reporting in long after the batch finished
    assert dispatched == [1, 2]   # no phantom re-dispatch


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


# ----------------------------------------------------------- moderation retry

def test_is_moderation_failure_matches_common_provider_wording():
    assert _is_moderation_failure("Blocked by content moderation policy")
    assert _is_moderation_failure("SAFETY: possibly safety-filtered")
    assert _is_moderation_failure("Request rejected")
    assert _is_moderation_failure("prohibited content detected")
    assert _is_moderation_failure("BLOCKED")   # case-insensitive


def test_is_moderation_failure_does_not_match_unrelated_errors():
    assert not _is_moderation_failure("network timeout, please retry")
    assert not _is_moderation_failure("No API key set for OpenAI")
    assert not _is_moderation_failure("Rate limit exceeded")


def test_augment_prompt_for_model_appends_suffix_only_when_flagged():
    flagged = _FakeModel("google::gemini-3-pro-image", ["image_generate"], strict_ip_policy=True)
    plain = _FakeModel("openai::gpt-image-2", ["image_generate"], strict_ip_policy=False)

    augmented = _augment_prompt_for_model("A superhero flying over a city", flagged)
    assert augmented.startswith("A superhero flying over a city")
    assert "non-copyrighted" in augmented

    assert _augment_prompt_for_model("A superhero flying over a city", plain) == \
        "A superhero flying over a city"


def test_augment_prompt_for_model_tolerates_a_model_with_no_such_attribute():
    # _FakeModel always defines strict_ip_policy now, but real callers use
    # getattr(..., False) specifically so a model object that predates this
    # field (or a lightweight test double) doesn't raise.
    class BareModel:
        pass

    assert _augment_prompt_for_model("A scene", BareModel()) == "A scene"
