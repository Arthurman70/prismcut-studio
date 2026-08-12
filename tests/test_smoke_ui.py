"""Boots the real MainWindow offscreen (no display needed) and exercises a
few cross-cutting behaviors end-to-end rather than in isolation: that the
whole window still constructs after wiring undo/redo through every panel,
that a real undo/redo round-trip changes visible project state, that a
theme/density switch actually changes the live stylesheet, and that a toast
posts to the overlay. One shared `app`/`win` per test module keeps this fast
(QMainWindow construction is the expensive part)."""
import os
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# A fresh directory per test process, not a fixed shared path: this app
# writes real persistent files here (autosave, thumbnail cache, etc.), and
# a stray leftover autosave from a previous run can trigger a blocking
# "Recover unsaved work?" QMessageBox on the next MainWindow() construction
# - fatal under pytest, since nothing is there to click it.
os.environ.setdefault("PRISMCUT_DATA_DIR", tempfile.mkdtemp(prefix="prismcut-test-"))

import pytest
from PySide6.QtWidgets import QApplication

from prismcut.ui import theme as theme_mod
from prismcut.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def win():
    app = QApplication.instance() or QApplication([])
    theme_mod.apply_theme(app)
    w = MainWindow()
    w.show()
    yield w
    # MainWindow.closeEvent shows a real blocking "Save before closing?"
    # QMessageBox if the project is dirty with clips - fatal under pytest
    # (nothing there to click it). Tests intentionally dirty the project
    # (e.g. to exercise autosave), so force a clean slate before closing
    # rather than relying on every test to reset it.
    w.project.dirty = False
    w.close()


def test_main_window_boots_with_every_panel(win):
    assert win.timeline and win.chat and win.photo and win.audio and win.bin
    assert win.undo_stack is not None
    assert win.history_dock is not None
    assert win.movie is not None


def test_shortcut_registry_populated_and_cheat_sheet_opens(win):
    from prismcut.core import shortcuts
    assert len(shortcuts.REGISTRY) > 10  # menu actions self-register via MainWindow._act
    categories = shortcuts.by_category()
    assert "File" in categories and "Edit" in categories
    win._show_shortcuts()
    assert win._shortcuts_dialog.isVisible()
    win._shortcuts_dialog.close()


def test_fullscreen_toggle_hides_and_restores_docks(win):
    was_visible = win.bin_dock.isVisible()
    win.bin_dock.setVisible(True)
    win._toggle_fullscreen()
    assert win.isFullScreen()
    assert not win.bin_dock.isVisible()
    win._toggle_fullscreen()
    assert not win.isFullScreen()
    assert win.bin_dock.isVisible() == True
    win.bin_dock.setVisible(was_visible)


def test_undo_redo_round_trip_on_a_real_clip(win):
    img = win.project.add_media(__file__)  # any existing local file works for id/path purposes
    img.kind = "image"
    track = win.project.video_tracks()[-1]
    clip = win.project.add_clip(img.id, track.id, 0.0, 2.0)
    win.timeline.refresh(True)
    assert clip.id in win.project.clips

    win.timeline.delete_clip(clip.id)
    assert clip.id not in win.project.clips

    win.undo_stack.undo()
    assert clip.id in win.project.clips

    win.undo_stack.redo()
    assert clip.id not in win.project.clips


def test_autosave_writes_without_touching_the_real_save_path(win):
    import json

    from prismcut.core import paths

    img = win.project.add_media(__file__)
    img.kind = "image"
    track = win.project.video_tracks()[-1]
    win.project.add_clip(img.id, track.id, 0.0, 2.0)
    win.project.dirty = True
    real_path_before = win.project.path

    win._autosave_tick()

    auto = paths.autosave_path_for(win.project.path)
    assert auto.exists()
    saved = json.loads(auto.read_text(encoding="utf-8"))
    assert saved["app"] == "prismcut"
    # autosave must never hijack the project's real save path/dirty state -
    # that's a real bug class (next Ctrl+S would silently save to the
    # autosave slot instead of the user's actual file).
    assert win.project.path == real_path_before
    assert win.project.dirty is True
    # Clean up: an untitled-slot autosave left on disk would make the next
    # MainWindow() constructed in this process (e.g. by a later test) hit a
    # real blocking "Recover unsaved work?" QMessageBox on startup - fatal
    # under pytest, same failure mode fixed above for the `win` fixture itself.
    auto.unlink()
    win.project.dirty = False


def test_layout_persists_dock_visibility_across_windows(win):
    was_visible = win.fx_dock.isVisible()
    win.fx_dock.setVisible(False)
    win._save_layout()
    try:
        second = MainWindow()
        try:
            second._restore_layout()
            assert second.fx_dock.isVisible() is False
        finally:
            second.project.dirty = False
            second.close()
    finally:
        win.fx_dock.setVisible(was_visible)
        win._save_layout()   # don't leak this test's layout into later runs


def test_theme_switch_changes_live_stylesheet(win):
    app = QApplication.instance()
    win._set_theme("light")
    light_bg = theme_mod.BG
    light_qss = app.styleSheet()
    win._set_theme("dark")
    dark_bg = theme_mod.BG
    assert light_bg != dark_bg
    assert light_qss != app.styleSheet()
    win._set_theme("dark")  # leave state clean for any later tests


def test_density_switch_changes_live_stylesheet(win):
    app = QApplication.instance()
    win._set_density("compact")
    compact_qss = app.styleSheet()
    win._set_density("spacious")
    assert compact_qss != app.styleSheet()
    win._set_density("comfortable")


def test_toast_posts_to_overlay(win):
    win.toast("smoke test toast", "success", timeout_ms=60000)
    assert win._toast_overlay.isVisible()
    assert win._toast_overlay._lay.count() >= 1


def test_agent_mode_toggle_exists_and_is_off_by_default(win):
    assert win.agent is not None
    assert win.chat.agent_mode is not None
    assert win.chat.agent_mode.isChecked() is False


def _dispatch_from_thread(win, call, timeout=5.0):
    """AgentToolRunner.dispatch() uses a BlockingQueuedConnection, which
    deadlocks outright if called from the same thread that owns the
    QObject (Qt: blocking-queued within one thread is a documented
    deadlock, not just unsupported) - dispatch() must always be exercised
    from a genuinely different thread, exactly like the real ChatWorker
    does. Bounded by a hard deadline rather than an unbounded wait, so a
    broken connection fails the test instead of hanging the whole suite."""
    import threading

    result_box = []

    def worker():
        result_box.append(win.agent.dispatch(call))

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    app = QApplication.instance()
    deadline = time.time() + timeout
    while t.is_alive() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    t.join(timeout=1.0)
    assert not t.is_alive(), "dispatch() never returned - the cross-thread hop hung"
    assert len(result_box) == 1
    return result_box[0]


def test_agent_tool_dispatch_crosses_to_gui_thread_and_pushes_undo(win):
    from prismcut.providers.tools import ToolCall

    win.settings.set("confirm/suppress/agent_tool_create_track", True)  # skip the confirm dialog
    before_count = win.undo_stack.count()

    result = _dispatch_from_thread(
        win, ToolCall(id="t1", name="create_track", arguments={"kind": "audio"}))

    assert not result.is_error, result.content
    assert win.undo_stack.count() == before_count + 1  # create_track's undo command landed


def test_agent_tool_confirm_decline_is_reported_as_error_and_makes_no_change(win):
    from prismcut.providers.tools import ToolCall

    win.settings.set("confirm/suppress/agent_tool_create_track", False)
    before_count = win.undo_stack.count()

    # Monkeypatch confirm_destructive for just this call to simulate the
    # user clicking Cancel, without opening a real dialog in the test.
    # agent.py imports it locally (from ..ui.widgets.common import
    # confirm_destructive) inside _run_on_gui_thread, so patching the name
    # on the common module itself (not on prismcut.core.agent) is what
    # actually takes effect - and since _run_on_gui_thread runs on the main
    # thread (via the blocking-queued hop), this plain monkeypatch, done
    # here on the main thread before dispatch, is visible to it safely.
    from prismcut.ui.widgets import common as common_mod
    saved = common_mod.confirm_destructive
    common_mod.confirm_destructive = lambda *a, **k: False
    try:
        result = _dispatch_from_thread(
            win, ToolCall(id="t2", name="create_track", arguments={"kind": "video"}))
    finally:
        common_mod.confirm_destructive = saved

    # Checking the specific decline message (not just is_error) matters: a
    # broken dispatch() that silently lost the result would ALSO report
    # is_error=True via its "No result." fallback - this must distinguish
    # "correctly declined" from "the mechanism is broken."
    assert result.is_error
    assert "declined" in result.content.lower()
    assert win.undo_stack.count() == before_count  # nothing was pushed


# ------------------------------------------------------- movie pipeline UI

def test_model_combo_allow_none_offers_none_as_default_selection(win):
    from prismcut.ui.widgets.common import ModelCombo

    combo = ModelCombo(win.registry, win.settings, ("lip_sync",), role="test_allow_none_probe",
                       allow_none=True)
    assert combo.itemText(0).startswith("— None")
    assert combo.current_key() == ""
    assert combo.current_model() is None


def test_new_pipeline_dialog_constructs_and_optional_combos_default_to_none(win):
    from prismcut.ui.dialogs.new_pipeline_dialog import NewPipelineDialog

    dlg = NewPipelineDialog(win.registry, win.settings, win.jobs, win.get_adapter, win)
    assert dlg.lipsync_combo.current_model() is None
    assert dlg.audio_combo.current_model() is None   # Voice/TTS is optional too
    assert dlg.pipeline is None   # nothing created until _accept() runs
    dlg.close()


def test_new_pipeline_dialog_accept_requires_a_brief(win):
    from prismcut.ui.dialogs import new_pipeline_dialog as dlg_mod

    # _accept() shows a blocking QMessageBox.information() on invalid input -
    # fatal under pytest (offscreen, nothing to click), so stub it out and
    # just assert it WAS called rather than letting the real dialog run.
    calls = []
    saved = dlg_mod.QMessageBox.information
    dlg_mod.QMessageBox.information = staticmethod(lambda *a, **k: calls.append(a))
    try:
        dlg = dlg_mod.NewPipelineDialog(win.registry, win.settings, win.jobs, win.get_adapter, win)
        dlg.brief_edit.setPlainText("")   # empty brief - must be rejected
        dlg._accept()
        assert dlg.pipeline is None
        assert len(calls) == 1
        dlg.close()
    finally:
        dlg_mod.QMessageBox.information = saved


def test_new_pipeline_dialog_accept_builds_pipeline_with_chosen_models(win):
    from prismcut.ui.dialogs.new_pipeline_dialog import NewPipelineDialog

    dlg = NewPipelineDialog(win.registry, win.settings, win.jobs, win.get_adapter, win)
    dlg.name_edit.setText("My Test Movie")
    dlg.brief_edit.setPlainText("A short story about a lighthouse keeper.")
    dlg._accept()
    assert dlg.pipeline is not None
    assert dlg.pipeline.name == "My Test Movie"
    assert dlg.pipeline.brief == "A short story about a lighthouse keeper."
    assert dlg.pipeline.script_model and "::" in dlg.pipeline.script_model
    assert dlg.pipeline.image_model and dlg.pipeline.video_model
    # Voice/TTS and lip-sync are both optional and default to skipped.
    assert dlg.pipeline.audio_model == ""
    assert dlg.pipeline.lipsync_model == ""
    dlg.close()


def test_new_pipeline_dialog_accept_does_not_require_a_voice_model(win):
    """The historical bug: audio used to be validated as required, so a
    silent/no-narration movie couldn't be created at all."""
    from prismcut.ui.dialogs.new_pipeline_dialog import NewPipelineDialog

    dlg = NewPipelineDialog(win.registry, win.settings, win.jobs, win.get_adapter, win)
    dlg.brief_edit.setPlainText("A silent short film.")
    assert dlg.audio_combo.current_model() is None
    dlg._accept()
    assert dlg.pipeline is not None
    assert dlg.pipeline.audio_model == ""
    dlg.close()


def test_new_pipeline_dialog_accept_carries_through_a_chosen_voice_model(win):
    from prismcut.ui.dialogs.new_pipeline_dialog import NewPipelineDialog

    dlg = NewPipelineDialog(win.registry, win.settings, win.jobs, win.get_adapter, win)
    dlg.brief_edit.setPlainText("A movie with narration.")
    idx = dlg.audio_combo.findData("google::gemini-3.1-flash-tts-preview")
    assert idx >= 0, "expected TTS model missing from registry"
    dlg.audio_combo.setCurrentIndex(idx)
    dlg._accept()
    assert dlg.pipeline is not None
    assert dlg.pipeline.audio_model == "google::gemini-3.1-flash-tts-preview"
    dlg.close()


def _wait_until(predicate, timeout=5.0):
    app = QApplication.instance()
    deadline = time.time() + timeout
    while not predicate() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    return predicate()


def test_new_pipeline_dialog_enhance_brief_replaces_text_with_chat_result(win):
    """No real API call - a fake adapter stands in, but this still exercises
    the real jobs.submit() -> QThreadPool -> on_done round trip."""
    from prismcut.ui.dialogs.new_pipeline_dialog import NewPipelineDialog

    class FakeAdapter:
        def chat(self, model_id, messages, system="", temperature=0.7, **kwargs):
            return "A moody, rain-slicked alley at dusk, neon signs bleeding into puddles."

    dlg = NewPipelineDialog(win.registry, win.settings, win.jobs, lambda provider: FakeAdapter(), win)
    dlg.brief_edit.setPlainText("A detective walks down an alley.")
    dlg._enhance_brief()

    assert _wait_until(lambda: "neon signs" in dlg.brief_edit.toPlainText()), \
        "brief was never replaced with the enhanced text"
    assert dlg.enhance_btn.isEnabled()
    dlg.close()


def test_new_pipeline_dialog_enhance_brief_failure_shows_warning_and_reenables(win):
    from prismcut.ui.dialogs import new_pipeline_dialog as dlg_mod

    class FailingAdapter:
        def chat(self, *a, **k):
            raise RuntimeError("no key configured")

    calls = []
    saved = dlg_mod.QMessageBox.warning
    dlg_mod.QMessageBox.warning = staticmethod(lambda *a, **k: calls.append(a))
    try:
        dlg = dlg_mod.NewPipelineDialog(win.registry, win.settings, win.jobs,
                                        lambda provider: FailingAdapter(), win)
        original = "A detective walks down an alley."
        dlg.brief_edit.setPlainText(original)
        dlg._enhance_brief()
        assert _wait_until(lambda: len(calls) == 1)
        assert dlg.brief_edit.toPlainText() == original   # left untouched on failure
        assert dlg.enhance_btn.isEnabled()
        dlg.close()
    finally:
        dlg_mod.QMessageBox.warning = saved


def test_movie_pipeline_panel_loads_pipeline_and_builds_scene_rows(win):
    from prismcut.core.pipeline import MoviePipeline, new_scene

    pipeline = MoviePipeline(name="Smoke test movie", brief="A robot explores a city.",
                             script_model="google::gemini-3.6-flash", image_model="fal::img-test",
                             audio_model="fal::tts-test", video_model="fal::vid-test")
    pipeline.scenes = [new_scene(0), new_scene(1)]
    pipeline.scenes[0].script = "A robot wakes up in a quiet alley."
    pipeline.scenes[1].script = "The robot looks up at the skyline."

    win.movie._set_pipeline(pipeline)
    try:
        assert win.movie.run is not None
        assert len(win.movie._rows) == 2
        first_row = win.movie._rows[pipeline.scenes[0].id]
        assert "robot wakes up" in first_row.title.text()
        assert win.movie.images_btn.isEnabled()      # scenes exist, none imaged yet
        assert not win.movie.video_btn.isEnabled()   # no scene has an image yet
    finally:
        # Leave no dangling PipelineRun (holds Qt signal connections to a
        # pipeline object that would otherwise outlive this test).
        win.movie._set_pipeline(MoviePipeline(name="empty"))


def test_movie_pipeline_draftboard_override_marks_scene_source_user(win):
    from prismcut.core.pipeline import MoviePipeline, new_scene

    pipeline = MoviePipeline(name="Draftboard smoke test", brief="brief",
                             script_model="google::gemini-3.6-flash", image_model="fal::img-test",
                             audio_model="fal::tts-test", video_model="fal::vid-test")
    pipeline.scenes = [new_scene(0)]
    scene = pipeline.scenes[0]

    win.movie._set_pipeline(pipeline)
    try:
        assert scene.image.active is None
        win.movie.run.set_scene_image_override(scene.id, __file__)
        assert scene.image.active is not None
        assert scene.image.active.source == "user"
        row = win.movie._rows[scene.id]
        assert row.icon.text() == "🖼"   # image ready, video still pending
    finally:
        win.movie._set_pipeline(MoviePipeline(name="empty"))
        win.movie.run.pipeline.scenes = []


def test_movie_pipeline_retry_button_and_status_visible_when_scenes_empty(win):
    """The historical bug: a failed/never-run script breakdown left scenes
    empty with zero visible explanation and no way to retry - just two
    correctly-disabled-but-mysterious buttons."""
    from prismcut.core.pipeline import MoviePipeline

    pipeline = MoviePipeline(name="Stuck movie", brief="brief", script_model="google::gemini-3.6-flash",
                             image_model="fal::img-test", video_model="fal::vid-test")
    win.movie._set_pipeline(pipeline)
    try:
        # isHidden() (this widget's own explicit visibility flag) rather than
        # isVisible() (which also depends on the Movie Pipeline tab actually
        # being the active one, unrelated to what's under test here).
        assert not win.movie.retry_script_btn.isHidden()
        assert win.movie.retry_script_btn.isEnabled()
        assert win.movie.script_status.text()   # some persistent explanation, not blank
        assert not win.movie.images_btn.isEnabled()
        assert not win.movie.video_btn.isEnabled()
    finally:
        win.movie._set_pipeline(MoviePipeline(name="empty"))


def test_movie_pipeline_retry_button_hides_once_scenes_exist(win):
    from prismcut.core.pipeline import MoviePipeline, new_scene

    pipeline = MoviePipeline(name="Scripted movie", brief="brief", script_model="google::gemini-3.6-flash",
                             image_model="fal::img-test", video_model="fal::vid-test")
    pipeline.scenes = [new_scene(0)]
    win.movie._set_pipeline(pipeline)
    try:
        assert win.movie.retry_script_btn.isHidden()
        assert win.movie.script_status.text() == ""
    finally:
        win.movie._set_pipeline(MoviePipeline(name="empty"))


def test_movie_pipeline_script_breakdown_success_populates_scenes_and_clears_status(win):
    from prismcut.core.pipeline import MoviePipeline

    class FakeAdapter:
        def chat(self, model_id, messages, system="", temperature=0.7, **kwargs):
            return '[{"script": "A lighthouse at dawn.", "narration": ""}]'

    pipeline = MoviePipeline(name="Retry-success movie", brief="A lighthouse story.",
                             script_model="google::gemini-3.6-flash", image_model="fal::img-test",
                             video_model="fal::vid-test")
    win.movie._set_pipeline(pipeline)
    saved_get_adapter = win.get_adapter
    win.get_adapter = lambda provider: FakeAdapter()
    try:
        assert not win.movie.retry_script_btn.isHidden()
        win.movie._retry_script()
        assert _wait_until(lambda: len(win.movie.run.pipeline.scenes) == 1)
        assert win.movie.run.pipeline.scenes[0].script == "A lighthouse at dawn."
        assert _wait_until(lambda: win.movie.retry_script_btn.isHidden())
        assert win.movie.script_status.text() == ""
        assert win.movie.images_btn.isEnabled()
    finally:
        win.get_adapter = saved_get_adapter
        win.movie._set_pipeline(MoviePipeline(name="empty"))


def test_movie_pipeline_script_breakdown_failure_shows_persistent_error_not_just_toast(win):
    from prismcut.core.pipeline import MoviePipeline

    class RefusingAdapter:
        def chat(self, model_id, messages, system="", temperature=0.7, **kwargs):
            return "I'm not able to help write a breakdown for that request."

    pipeline = MoviePipeline(name="Refused movie", brief="brief", script_model="google::gemini-3.6-flash",
                             image_model="fal::img-test", video_model="fal::vid-test")
    win.movie._set_pipeline(pipeline)
    saved_get_adapter = win.get_adapter
    win.get_adapter = lambda provider: RefusingAdapter()
    try:
        win.movie._retry_script()
        assert _wait_until(lambda: not win.movie._script_running)
        assert win.movie.run.pipeline.scenes == []
        assert not win.movie.retry_script_btn.isHidden()
        assert win.movie.retry_script_btn.isEnabled()   # can try again, not stuck disabled
        assert "not able to help" in win.movie.script_status.text()
    finally:
        win.get_adapter = saved_get_adapter
        win.movie._set_pipeline(MoviePipeline(name="empty"))


def test_movie_pipeline_gate_buttons_give_a_clear_message_when_scenes_are_empty(win):
    """Regression test for the original misleading message: clicking a
    (disabled) generate button on a script-less movie used to be guarded by
    code that would have said "Every scene already has an image/video" -
    nonsensical for zero scenes. Calling the handlers directly (bypassing the
    disabled state, same as the real bug report's symptom) must report the
    real reason, not the misleading one."""
    from prismcut.core.pipeline import MoviePipeline

    pipeline = MoviePipeline(name="Empty movie", brief="brief", script_model="google::gemini-3.6-flash",
                             image_model="fal::img-test", video_model="fal::vid-test")
    win.movie._set_pipeline(pipeline)
    messages = []
    win.movie.status.connect(messages.append)
    try:
        win.movie._run_images()
        win.movie._run_video()
        assert len(messages) == 2
        for m in messages:
            assert "already has" not in m
            assert "breakdown" in m
    finally:
        win.movie.status.disconnect(messages.append)
        win.movie._set_pipeline(MoviePipeline(name="empty"))


# -------------------------------------------------- timeline integration

def test_movie_pipeline_scenes_get_labeled_clips_on_timeline(win):
    from prismcut.core.pipeline import MoviePipeline, StageAsset, new_scene

    pipeline = MoviePipeline(name="Label test", brief="brief", script_model="google::gemini-3.6-flash",
                             image_model="google::gemini-3.1-flash-image",
                             video_model="xai::grok-imagine-video-1.5")
    pipeline.scenes = [new_scene(0), new_scene(1)]
    win.movie._set_pipeline(pipeline)
    run = win.movie.run
    try:
        for scene in pipeline.scenes:
            item = win.bin.add_generated(__file__, {"mode": "image"})
            scene.image.push(StageAsset(media_id=item.id, source="generated"))
        run._images_done = True
        run._audio_done = True
        run._maybe_insert_all_interim()
        clip1 = win.project.clips[pipeline.scenes[0].clip_ids["image"]]
        clip2 = win.project.clips[pipeline.scenes[1].clip_ids["image"]]
        assert clip1.label == "Scene 1"
        assert clip2.label == "Scene 2"
    finally:
        win.movie._set_pipeline(MoviePipeline(name="empty"))


def test_swap_scene_visual_clip_replaces_old_clip_and_updates_clip_ids(win):
    from prismcut.core.pipeline import MoviePipeline, new_scene

    pipeline = MoviePipeline(name="Swap test", brief="brief", script_model="google::gemini-3.6-flash",
                             image_model="google::gemini-3.1-flash-image",
                             video_model="xai::grok-imagine-video-1.5")
    pipeline.scenes = [new_scene(0)]
    scene = pipeline.scenes[0]
    win.movie._set_pipeline(pipeline)
    run = win.movie.run
    try:
        run._ensure_tracks()
        img_item = win.bin.add_generated(__file__, {"mode": "image"})
        old_clip = win.timeline.add_media_at_playhead(
            img_item.id, pipeline.video_track_id, 5.0, 4.0, label="Scene 1")
        scene.clip_ids["image"] = old_clip.id

        vid_item = win.bin.add_generated(__file__, {"mode": "video"})
        run._swap_scene_visual_clip(scene, vid_item, "video", "test swap")

        assert old_clip.id not in win.project.clips
        assert "image" not in scene.clip_ids
        new_clip = win.project.clips[scene.clip_ids["video"]]
        assert new_clip.media_id == vid_item.id
        assert new_clip.start == 5.0
        assert new_clip.duration == 4.0
        assert new_clip.label == "Scene 1"
    finally:
        win.movie._set_pipeline(MoviePipeline(name="empty"))


def test_regenerate_image_swaps_the_already_placed_timeline_clip(win):
    """The actual bug: regenerating an image used to update scene.image but
    leave the stale image sitting on the timeline untouched."""
    from prismcut.core.pipeline import MoviePipeline, StageAsset, new_scene

    class FakeImageAdapter:
        def generate_image(self, model_id, prompt, params):
            return [__file__]

    pipeline = MoviePipeline(name="Image regen test", brief="brief",
                             script_model="google::gemini-3.6-flash",
                             image_model="google::gemini-3.1-flash-image",
                             video_model="xai::grok-imagine-video-1.5")
    pipeline.scenes = [new_scene(0)]
    scene = pipeline.scenes[0]
    win.movie._set_pipeline(pipeline)
    run = win.movie.run
    saved_get_adapter = win.get_adapter
    win.get_adapter = lambda provider: FakeImageAdapter()
    try:
        run._ensure_tracks()
        first_item = win.bin.add_generated(__file__, {"mode": "image"})
        scene.image.push(StageAsset(media_id=first_item.id, source="generated"))
        old_clip = win.timeline.add_media_at_playhead(
            first_item.id, pipeline.video_track_id, 0.0, 3.0, label="Scene 1")
        scene.clip_ids["image"] = old_clip.id

        run.regenerate_scene_current_stage(scene.id)
        assert _wait_until(lambda: old_clip.id not in win.project.clips)

        new_clip = win.project.clips[scene.clip_ids["image"]]
        assert new_clip.start == 0.0
        assert new_clip.label == "Scene 1"
    finally:
        win.get_adapter = saved_get_adapter
        win.movie._set_pipeline(MoviePipeline(name="empty"))


def test_regenerate_current_stage_regenerates_video_when_video_already_exists(win):
    from prismcut.core.pipeline import MoviePipeline, StageAsset, new_scene

    class FakeVideoAdapter:
        def generate_video(self, model_id, prompt, params, **kwargs):
            return __file__

    pipeline = MoviePipeline(name="Video regen test", brief="brief",
                             script_model="google::gemini-3.6-flash",
                             image_model="google::gemini-3.1-flash-image",
                             video_model="xai::grok-imagine-video-1.5")
    pipeline.scenes = [new_scene(0)]
    scene = pipeline.scenes[0]
    win.movie._set_pipeline(pipeline)
    run = win.movie.run
    saved_get_adapter = win.get_adapter
    win.get_adapter = lambda provider: FakeVideoAdapter()
    try:
        run._ensure_tracks()
        first_vid = win.bin.add_generated(__file__, {"mode": "video"})
        scene.video.push(StageAsset(media_id=first_vid.id, source="generated"))
        old_clip = win.timeline.add_media_at_playhead(
            first_vid.id, pipeline.video_track_id, 2.0, 5.0, label="Scene 1")
        scene.clip_ids["video"] = old_clip.id

        run.regenerate_scene_current_stage(scene.id)
        assert _wait_until(lambda: old_clip.id not in win.project.clips)

        new_clip = win.project.clips[scene.clip_ids["video"]]
        assert new_clip.id != old_clip.id
        assert new_clip.start == 2.0
    finally:
        win.get_adapter = saved_get_adapter
        win.movie._set_pipeline(MoviePipeline(name="empty"))


def test_timeline_reveal_clip_seeks_scrolls_and_selects(win):
    img = win.project.add_media(__file__)
    img.kind = "image"
    track = win.project.video_tracks()[-1]
    clip = win.project.add_clip(img.id, track.id, 12.5, 3.0)
    win.timeline.refresh(True)
    try:
        assert win.timeline.reveal_clip(clip.id) is True
        assert win.timeline.playhead_time == 12.5
        assert win.timeline.selected_clip() is clip
    finally:
        win.project.remove_media(img.id)
        win.timeline.refresh(True)


def test_timeline_reveal_clip_returns_false_for_unknown_clip(win):
    assert win.timeline.reveal_clip("nonexistent-clip-id") is False


def test_movie_pipeline_jump_to_scene_seeks_timeline_and_switches_tab(win):
    from prismcut.core.pipeline import MoviePipeline, StageAsset, new_scene

    pipeline = MoviePipeline(name="Jump test", brief="brief", script_model="google::gemini-3.6-flash",
                             image_model="google::gemini-3.1-flash-image",
                             video_model="xai::grok-imagine-video-1.5")
    pipeline.scenes = [new_scene(0)]
    scene = pipeline.scenes[0]
    win.movie._set_pipeline(pipeline)
    run = win.movie.run
    try:
        run._ensure_tracks()
        item = win.bin.add_generated(__file__, {"mode": "image"})
        scene.image.push(StageAsset(media_id=item.id, source="generated"))
        clip = win.timeline.add_media_at_playhead(
            item.id, pipeline.video_track_id, 7.0, 3.0, label="Scene 1")
        scene.clip_ids["image"] = clip.id

        win.tabs.setCurrentIndex(1)   # somewhere else, so the switch is observable
        win.movie._jump_to_scene(scene.id)
        assert win.timeline.playhead_time == 7.0
        assert win.tabs.currentIndex() == 0
    finally:
        win.movie._set_pipeline(MoviePipeline(name="empty"))


def test_movie_pipeline_jump_to_scene_not_placed_yet_gives_a_clear_message(win):
    from prismcut.core.pipeline import MoviePipeline, new_scene

    pipeline = MoviePipeline(name="Unplaced jump test", brief="brief",
                             script_model="google::gemini-3.6-flash",
                             image_model="google::gemini-3.1-flash-image",
                             video_model="xai::grok-imagine-video-1.5")
    pipeline.scenes = [new_scene(0)]
    win.movie._set_pipeline(pipeline)
    messages = []
    win.movie.status.connect(messages.append)
    try:
        win.movie._jump_to_scene(pipeline.scenes[0].id)
        assert any("timeline" in m for m in messages)
    finally:
        win.movie.status.disconnect(messages.append)
        win.movie._set_pipeline(MoviePipeline(name="empty"))


def test_regenerate_pipeline_scene_uses_currently_loaded_pipeline_directly(win):
    from prismcut.core.pipeline import MoviePipeline, StageAsset, new_scene

    class FakeImageAdapter:
        def generate_image(self, model_id, prompt, params):
            return [__file__]

    pipeline = MoviePipeline(name="Direct regen test", brief="brief",
                             script_model="google::gemini-3.6-flash",
                             image_model="google::gemini-3.1-flash-image",
                             video_model="xai::grok-imagine-video-1.5")
    pipeline.scenes = [new_scene(0)]
    scene = pipeline.scenes[0]
    win.movie._set_pipeline(pipeline)
    saved_get_adapter = win.get_adapter
    win.get_adapter = lambda provider: FakeImageAdapter()
    try:
        win.movie.run._ensure_tracks()
        item = win.bin.add_generated(__file__, {"mode": "image"})
        scene.image.push(StageAsset(media_id=item.id, source="generated"))
        old_clip = win.timeline.add_media_at_playhead(
            item.id, pipeline.video_track_id, 0.0, 3.0, label="Scene 1")
        scene.clip_ids["image"] = old_clip.id

        win.tabs.setCurrentIndex(1)
        win._regenerate_pipeline_scene(pipeline.id, scene.id)
        assert win.tabs.currentIndex() == win.tabs.indexOf(win.movie)
        assert _wait_until(lambda: old_clip.id not in win.project.clips)
    finally:
        win.get_adapter = saved_get_adapter
        win.movie._set_pipeline(MoviePipeline(name="empty"))


def test_regenerate_pipeline_scene_loads_a_different_saved_pipeline_first(win):
    from prismcut.core.pipeline import MoviePipeline, new_scene

    other = MoviePipeline(name="Other saved movie", brief="brief",
                          script_model="google::gemini-3.6-flash",
                          image_model="google::gemini-3.1-flash-image",
                          video_model="xai::grok-imagine-video-1.5")
    other.scenes = [new_scene(0)]
    other.save()

    current = MoviePipeline(name="Currently open movie")
    win.movie._set_pipeline(current)
    win.tabs.setCurrentIndex(1)

    class NoopAdapter:
        def generate_image(self, *a, **k):
            return []   # "no image returned" - fine, only the load+switch is under test here

    saved_get_adapter = win.get_adapter
    win.get_adapter = lambda provider: NoopAdapter()
    try:
        win._regenerate_pipeline_scene(other.id, other.scenes[0].id)
        assert win.movie.run.pipeline.id == other.id
        assert win.tabs.currentIndex() == win.tabs.indexOf(win.movie)
    finally:
        win.get_adapter = saved_get_adapter
        win.movie._set_pipeline(MoviePipeline(name="empty"))
        from prismcut.core import paths as paths_mod
        p = paths_mod.pipelines_dir() / f"{other.id}.json"
        if p.exists():
            p.unlink()


def test_movie_pipeline_load_by_id_reports_missing_file_gracefully(win):
    messages = []
    win.movie.status.connect(messages.append)
    try:
        ok = win.movie.load_pipeline_by_id("does-not-exist-12345")
        assert ok is False
        assert any("find" in m.lower() for m in messages)
    finally:
        win.movie.status.disconnect(messages.append)


# ------------------------------------------------------------------ updater

def test_update_dialog_offers_download_when_release_has_an_installer_asset(win):
    from prismcut.core.updater import ReleaseInfo
    from prismcut.ui.dialogs.update_dialog import UpdateDialog

    release = ReleaseInfo(tag="v99.0.0", name="PrismCut 99.0.0", notes="### New\n- big stuff",
                          html_url="https://example.test/releases/v99.0.0",
                          asset_url="https://example.test/setup.exe", asset_name="Setup.exe")
    dlg = UpdateDialog(release, win.jobs, win.settings, win)
    try:
        # This dev/test environment is never the Inno-Setup-installed copy,
        # so installed_location() is None here - the button should be the
        # manual "Download Installer" path, not the silent self-update one.
        assert dlg.go is not None
        assert "Download" in dlg.go.text()
    finally:
        dlg.close()


def test_update_dialog_offers_no_action_button_when_release_has_no_asset(win):
    from prismcut.core.updater import ReleaseInfo
    from prismcut.ui.dialogs.update_dialog import UpdateDialog

    release = ReleaseInfo(tag="v99.0.0", name="PrismCut 99.0.0", notes="",
                          html_url="https://example.test/releases/v99.0.0")
    dlg = UpdateDialog(release, win.jobs, win.settings, win)
    try:
        assert dlg.go is None   # nothing this platform can download - link only
    finally:
        dlg.close()


def test_update_dialog_skip_version_persists_to_settings(win):
    from prismcut.core.updater import ReleaseInfo
    from prismcut.ui.dialogs.update_dialog import UpdateDialog

    win.settings.set("updater/skip_version", "")
    release = ReleaseInfo(tag="v42.0.0", name="v42.0.0", notes="", html_url="https://example.test")
    dlg = UpdateDialog(release, win.jobs, win.settings, win)
    try:
        dlg.skip_check.setChecked(True)
        dlg._remind_later()
        assert win.settings.get("updater/skip_version") == "v42.0.0"
    finally:
        win.settings.set("updater/skip_version", "")


def test_check_for_updates_is_wired_into_the_help_menu(win):
    assert callable(win._check_updates)


# ------------------------------------------------------------------- export

def test_export_dialog_offers_the_full_format_array(win):
    from prismcut.ui.dialogs.export_dialog import FORMATS, ExportDialog

    keys = [key for _name, key, _ext in FORMATS]
    for expected in ("mp4", "mp4-hevc", "mov", "mov-prores", "mkv", "webm",
                     "gif", "png-seq", "mp3", "wav", "flac", "m4a"):
        assert expected in keys, f"{expected!r} missing from the export format array"

    dlg = ExportDialog(win.project, win.jobs, win.settings, win)
    try:
        assert dlg.fmt.count() == len(FORMATS)
    finally:
        dlg.close()


def test_export_dialog_format_switch_updates_output_extension(win):
    from prismcut.ui.dialogs.export_dialog import FORMATS, ExportDialog

    dlg = ExportDialog(win.project, win.jobs, win.settings, win)
    try:
        for idx, (_name, _key, ext) in enumerate(FORMATS):
            dlg.fmt.setCurrentIndex(idx)
            assert dlg.out_edit.text().endswith(ext), f"format row {idx} didn't update the extension"
    finally:
        dlg.close()
    assert callable(win._maybe_check_updates_on_startup)
