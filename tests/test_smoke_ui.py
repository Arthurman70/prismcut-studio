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


def test_main_applies_theme_with_real_settings_not_none(monkeypatch):
    """Regression test: app.py used to call theme.apply_theme(app) with no
    settings arg, so apply_theme's own "read the saved theme" branch never
    ran and it always fell back to following the OS light/dark preference -
    the "starts in light mode even with dark mode saved" bug."""
    import prismcut.app as app_mod
    from prismcut.core.settings import Settings
    from prismcut.ui import theme as theme_mod
    from prismcut.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])   # same idiom the win fixture uses
    monkeypatch.setattr(QApplication, "exec", lambda self: 0)
    calls = []
    real_apply_theme = theme_mod.apply_theme

    def spy_apply_theme(qapp, settings=None):
        calls.append(settings)
        return real_apply_theme(qapp, settings)
    monkeypatch.setattr(theme_mod, "apply_theme", spy_apply_theme)

    before = set(app.topLevelWidgets())
    try:
        app_mod.main(app)   # pass the existing instance - only one QApplication per process
        assert len(calls) == 1
        assert calls[0] is not None
        assert isinstance(calls[0], Settings)
    finally:
        for w in app.topLevelWidgets():
            if w not in before and isinstance(w, MainWindow):
                w.project.dirty = False
                w.close()


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


# --------------------------------------------------------------- monitor/seek

class _FakePlayer:
    """Stands in for QMediaPlayer so these tests exercise seek_seconds()'s
    deferral LOGIC without depending on real media file decoding or codec
    availability."""

    def __init__(self, status=None):
        self._status = status
        self.positions = []

    def mediaStatus(self):
        return self._status

    def setPosition(self, ms):
        self.positions.append(ms)


def test_monitor_seek_seconds_seeks_immediately_when_media_ready(win):
    from PySide6.QtMultimedia import QMediaPlayer

    mon = win.project_monitor
    if mon.player is None:
        pytest.skip("QtMultimedia backend unavailable in this environment")
    fake = _FakePlayer(QMediaPlayer.MediaStatus.LoadedMedia)
    saved = mon.player
    mon.player = fake
    try:
        mon.seek_seconds(2.5)
        assert fake.positions == [2500]
        assert mon._pending_seek is None
    finally:
        mon.player = saved


def test_monitor_seek_seconds_defers_until_media_actually_loaded(win):
    """The actual fix: a seek issued right after switching sources used to
    be silently dropped because QtMultimedia loads media asynchronously -
    the concrete cause of timeline scrub-preview freezing at scene
    boundaries instead of showing/advancing into the next scene."""
    from PySide6.QtMultimedia import QMediaPlayer

    mon = win.project_monitor
    if mon.player is None:
        pytest.skip("QtMultimedia backend unavailable in this environment")
    fake = _FakePlayer(QMediaPlayer.MediaStatus.LoadingMedia)
    saved = mon.player
    mon.player = fake
    try:
        mon.seek_seconds(4.0)
        assert fake.positions == []          # not applied yet - media isn't ready
        assert mon._pending_seek == 4.0

        mon._media_status_changed(QMediaPlayer.MediaStatus.LoadingMedia)   # still not ready
        assert fake.positions == []
        assert mon._pending_seek == 4.0

        mon._media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)   # now ready
        assert fake.positions == [4000]
        assert mon._pending_seek is None
    finally:
        mon.player = saved


def test_monitor_media_status_changed_is_a_noop_without_a_pending_seek(win):
    from PySide6.QtMultimedia import QMediaPlayer

    mon = win.project_monitor
    if mon.player is None:
        pytest.skip("QtMultimedia backend unavailable in this environment")
    fake = _FakePlayer()
    saved = mon.player
    mon.player = fake
    mon._pending_seek = None
    try:
        mon._media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)
        assert fake.positions == []
    finally:
        mon.player = saved


# -------------------------------------------------- monitor/second audio player

def test_project_monitor_audio_seek_seconds_seeks_immediately_when_ready(win):
    """Same deferred-seek contract as the primary player (see above),
    duplicated for the secondary audio-only player that mixes in sound
    from a separate audio-track clip resolve_at() never looks at."""
    from PySide6.QtMultimedia import QMediaPlayer

    mon = win.project_monitor
    if mon.audio_player is None:
        pytest.skip("QtMultimedia backend unavailable in this environment")
    fake = _FakePlayer(QMediaPlayer.MediaStatus.LoadedMedia)
    saved = mon.audio_player
    mon.audio_player = fake
    try:
        mon._audio_seek_seconds(1.5)
        assert fake.positions == [1500]
        assert mon._pending_audio_seek is None
    finally:
        mon.audio_player = saved


def test_project_monitor_audio_seek_seconds_defers_until_ready(win):
    from PySide6.QtMultimedia import QMediaPlayer

    mon = win.project_monitor
    if mon.audio_player is None:
        pytest.skip("QtMultimedia backend unavailable in this environment")
    fake = _FakePlayer(QMediaPlayer.MediaStatus.LoadingMedia)
    saved = mon.audio_player
    mon.audio_player = fake
    try:
        mon._audio_seek_seconds(3.0)
        assert fake.positions == []
        assert mon._pending_audio_seek == 3.0

        mon._audio_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)
        assert fake.positions == [3000]
        assert mon._pending_audio_seek is None
    finally:
        mon.audio_player = saved


def test_project_monitor_preview_at_mixes_in_separate_audio_track_clip(win):
    """The actual feature: previewing/scrubbing the timeline now plays
    sound from a clip on the audio track (a video's auto-split companion,
    background music, narration, ...) even though resolve_at() - which
    decides what picture to show - never looks at audio tracks at all."""
    from prismcut.core.project import Project

    mon = win.project_monitor
    if mon.audio_player is None:
        pytest.skip("QtMultimedia backend unavailable in this environment")
    saved_project = mon.project
    p = Project()
    img_item = p.add_media(__file__)   # content doesn't matter, existence does
    img_item.kind = "image"
    aud_item = p.add_media(__file__, group="audio")
    aud_item.kind = "audio"
    v1 = p.video_tracks()[-1]
    a1 = p.audio_tracks()[0]
    p.add_clip(img_item.id, v1.id, 0.0, 5.0)
    p.add_clip(aud_item.id, a1.id, 0.0, 5.0)
    mon.set_project(p)
    try:
        mon.preview_at(2.0)
        assert mon._preview_audio_media_id == aud_item.id

        mon.preview_at(10.0)   # past both clips
        assert mon._preview_audio_media_id is None
    finally:
        mon.set_project(saved_project)


def test_project_monitor_mutes_primary_player_for_strip_audio_clips(win):
    """Bug fix: previewing a split video clip (Clip.strip_audio=True) used
    to play its own embedded audio through the primary player AND its
    split companion through the second player at the same time - doubled,
    out-of-phase sound. The primary player must mute for such clips."""
    from prismcut.core.project import Project

    mon = win.project_monitor
    if mon.player is None:
        pytest.skip("QtMultimedia backend unavailable in this environment")
    saved_project = mon.project
    p = Project()
    vid_item = p.add_media(__file__)
    vid_item.kind = "video"
    v1 = p.video_tracks()[-1]
    normal_clip = p.add_clip(vid_item.id, v1.id, 0.0, 3.0)
    split_clip = p.add_clip(vid_item.id, v1.id, 3.0, 3.0)
    split_clip.strip_audio = True
    mon.set_project(p)
    try:
        mon.preview_at(1.0)   # over the normal (non-split) clip
        assert mon.audio_out.isMuted() is False

        mon.preview_at(4.0)   # over the split clip
        assert mon.audio_out.isMuted() is True

        mon.preview_at(1.0)   # back to the normal clip - must unmute again
        assert mon.audio_out.isMuted() is False
    finally:
        mon.set_project(saved_project)


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


def test_model_combo_shows_price_hint_for_priced_models(win):
    """The new pricing feature: each model's dropdown entry carries a
    short rate label so cost is visible right where you pick the model,
    not just in the Movie Pipeline's confirm dialogs."""
    from prismcut.ui.widgets.common import ModelCombo

    combo = ModelCombo(win.registry, win.settings, ("video_generate",), role="test_price_probe")
    idx = combo.findData("google::veo-3.1-generate-preview")
    assert idx >= 0
    assert "$0.4/s" in combo.itemText(idx)


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


def test_new_pipeline_dialog_shows_cost_estimate_as_models_are_picked(win):
    """The new cost-estimation feature: a running 'estimated cost for N
    scenes' preview that updates as image/video/voice models and the
    scene-count preview are changed."""
    from prismcut.ui.dialogs.new_pipeline_dialog import NewPipelineDialog

    dlg = NewPipelineDialog(win.registry, win.settings, win.jobs, win.get_adapter, win)
    try:
        idx = dlg.image_combo.findData("google::gemini-3.1-flash-image")
        assert idx >= 0
        dlg.image_combo.setCurrentIndex(idx)
        assert "images ~$" in dlg.cost_label.text()

        idx = dlg.video_combo.findData("xai::grok-imagine-video-1.5")
        assert idx >= 0
        dlg.video_combo.setCurrentIndex(idx)
        assert "video ~$" in dlg.cost_label.text()
        assert f"for {dlg.est_scenes.value()} scenes" in dlg.cost_label.text()

        dlg.est_scenes.setValue(20)
        assert "for 20 scenes" in dlg.cost_label.text()
    finally:
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
        def generate_image(self, model_id, prompt, params, refs=None):
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


def test_regenerate_video_swap_uses_the_new_videos_real_duration(win, monkeypatch, tmp_path):
    """The actual bug: swapping in a finished video used to always inherit
    the OLD (interim image+audio placeholder) clip's duration, ignoring
    the new video's own real (already-probed) length - meaning a scene's
    requested video_params["duration"] (the per-scene length control from
    the Details panel, task #59/#60) had no visible effect on the
    timeline no matter what the AI actually generated."""
    from prismcut.core.pipeline import MoviePipeline, new_scene
    from prismcut.core import project as project_mod

    video_path = tmp_path / "fresh_video.mp4"
    video_path.write_bytes(b"fake-mp4-bytes")
    real_probe = project_mod.media_utils.probe

    def fake_probe(path):
        if str(path) == str(video_path):
            return {"duration": 9.5, "width": 1920, "height": 1080,
                    "has_audio": True, "has_video": True}
        return real_probe(path)

    monkeypatch.setattr(project_mod.media_utils, "probe", fake_probe)

    class FakeVideoAdapter:
        def generate_video(self, model_id, prompt, params, **kwargs):
            return str(video_path)

    pipeline = MoviePipeline(name="Duration fix test", brief="brief",
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
        img_item = win.bin.add_generated(__file__, {"mode": "image"})
        old_clip = win.timeline.add_media_at_playhead(
            img_item.id, pipeline.video_track_id, 0.0, 3.0, label="Scene 1")
        scene.clip_ids["image"] = old_clip.id

        run.regenerate_scene_video(scene.id)
        assert _wait_until(lambda: old_clip.id not in win.project.clips)

        new_clip = win.project.clips[scene.clip_ids["video"]]
        assert new_clip.duration == 9.5
    finally:
        win.get_adapter = saved_get_adapter
        win.movie._set_pipeline(MoviePipeline(name="empty"))


def test_scene_audio_generation_uses_the_configured_voice(win):
    """Bug fix: _generate_scene_audio used to hardcode voice="" positionally
    regardless of the model's configured voice - every TTS adapter resolves
    voice from that positional arg, not from params, so every scene's
    narration silently used each provider's hardcoded fallback voice
    instead of whatever was actually configured for the model."""
    from prismcut.core.pipeline import MoviePipeline, new_scene

    captured = []

    class CapturingTTSAdapter:
        def tts(self, model_id, text, voice, params):
            captured.append(voice)
            return __file__

    pipeline = MoviePipeline(name="Voice test", brief="brief",
                             script_model="google::gemini-3.6-flash",
                             image_model="google::gemini-3.1-flash-image",
                             video_model="xai::grok-imagine-video-1.5",
                             audio_model="minimax::speech-02-hd")
    pipeline.scenes = [new_scene(0)]
    pipeline.scenes[0].narration = "Hello there."
    win.movie._set_pipeline(pipeline)
    saved_get_adapter = win.get_adapter
    win.get_adapter = lambda provider: CapturingTTSAdapter()
    try:
        win.movie.run.run_audio_batch()
        assert _wait_until(lambda: len(captured) == 1)
        assert captured[0] == "Wise_Woman"   # the model's registry-configured default voice
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


def test_scene_image_params_override_merges_with_model_defaults(win):
    from prismcut.core.pipeline import MoviePipeline, new_scene

    captured = []

    class CapturingImageAdapter:
        def generate_image(self, model_id, prompt, params, refs=None):
            captured.append(dict(params))
            return [__file__]

    pipeline = MoviePipeline(name="Param override test", brief="brief",
                             script_model="google::gemini-3.6-flash",
                             image_model="google::gemini-3.1-flash-image",
                             video_model="xai::grok-imagine-video-1.5")
    pipeline.scenes = [new_scene(0)]
    scene = pipeline.scenes[0]
    scene.image_params = {"aspect_ratio": "9:16"}   # override just this one field
    win.movie._set_pipeline(pipeline)
    saved_get_adapter = win.get_adapter
    win.get_adapter = lambda provider: CapturingImageAdapter()
    try:
        win.movie.run.regenerate_scene_image(scene.id)
        assert _wait_until(lambda: len(captured) == 1)
        assert captured[0]["aspect_ratio"] == "9:16"
        assert captured[0]["image_size"] == "1K"   # untouched model default still present
    finally:
        win.get_adapter = saved_get_adapter
        win.movie._set_pipeline(MoviePipeline(name="empty"))


def test_param_form_int_field_default_max_is_full_int32_range(win):
    """Bug fix: `1 << 31 - 1` parses as `1 << (31 - 1)` (2^30), not the
    evidently-intended `(1 << 31) - 1` (2^31-1) - operator-precedence
    bug. Harmless in practice (no real model param approaches either
    bound) but worth pinning down."""
    from prismcut.core.registry import ModelSpec
    from prismcut.ui.panels.generate_panel import ParamForm

    spec = ModelSpec(id="x", provider="test", params=[
        {"name": "seed", "label": "Seed", "type": "int", "default": 0}])
    form = ParamForm()
    form.build(spec)
    assert form.widgets["seed"].maximum() == (1 << 31) - 1


def test_scene_video_params_override_merges_with_model_defaults(win):
    from prismcut.core.pipeline import MoviePipeline, new_scene

    captured = []

    class CapturingVideoAdapter:
        def generate_video(self, model_id, prompt, params, **kwargs):
            captured.append(dict(params))
            return __file__

    pipeline = MoviePipeline(name="Video param override test", brief="brief",
                             script_model="google::gemini-3.6-flash",
                             image_model="google::gemini-3.1-flash-image",
                             video_model="xai::grok-imagine-video-1.5")
    pipeline.scenes = [new_scene(0)]
    scene = pipeline.scenes[0]
    scene.video_params = {"duration": 3}   # shorter clip than the model's default of 6
    win.movie._set_pipeline(pipeline)
    saved_get_adapter = win.get_adapter
    win.get_adapter = lambda provider: CapturingVideoAdapter()
    try:
        win.movie.run.regenerate_scene_video(scene.id)
        assert _wait_until(lambda: len(captured) == 1)
        assert captured[0]["duration"] == 3
        assert captured[0]["resolution"] == "720p"   # untouched model default still present
    finally:
        win.get_adapter = saved_get_adapter
        win.movie._set_pipeline(MoviePipeline(name="empty"))


def test_image_batch_generates_sequentially_with_reference_context(win):
    """The actual feature: scene images generate one after another (never
    in parallel), and each scene after the first gets earlier scenes'
    images passed as reference context for visual consistency. This is a
    single test for both properties, not two: if generation were still
    running in parallel, a later scene's refs would come back empty/wrong
    (the earlier scene wouldn't have finished yet to have an active image),
    so correct refs are only possible if the chain is genuinely sequential."""
    from prismcut.core.pipeline import MoviePipeline, new_scene

    calls = []

    class FakeSequentialAdapter:
        def generate_image(self, model_id, prompt, params, refs=None):
            calls.append(list(refs) if refs else [])
            return [__file__]

    pipeline = MoviePipeline(name="Sequential test", brief="brief",
                             script_model="google::gemini-3.6-flash",
                             image_model="google::gemini-3.1-flash-image",
                             video_model="xai::grok-imagine-video-1.5")
    pipeline.scenes = [new_scene(0), new_scene(1), new_scene(2)]
    win.movie._set_pipeline(pipeline)
    saved_get_adapter = win.get_adapter
    win.get_adapter = lambda provider: FakeSequentialAdapter()
    try:
        win.movie.run.run_image_batch()
        assert _wait_until(lambda: all(s.image.active for s in pipeline.scenes), timeout=10.0)
        assert len(calls) == 3
        assert calls[0] == []                # scene 1: no earlier scenes to reference yet
        assert len(calls[1]) == 1            # scene 2: references scene 1 (anchor == previous)
        assert len(calls[2]) == 2            # scene 3: references scene 1 (anchor) + scene 2 (previous)
    finally:
        win.get_adapter = saved_get_adapter
        win.movie._set_pipeline(MoviePipeline(name="empty"))


def test_scene_row_details_panel_prefills_and_saves_script_and_params(win):
    """The per-scene review/edit panel (collapsible 'Details' section): it
    should pre-fill the script box and both param forms from the scene's
    current script/overrides, and 'Save changes' should write edits for all
    three back onto the Scene object (which regeneration then reads)."""
    from prismcut.core.pipeline import MoviePipeline, new_scene

    pipeline = MoviePipeline(name="Details panel test", brief="brief",
                             script_model="google::gemini-3.6-flash",
                             image_model="google::gemini-3.1-flash-image",
                             video_model="xai::grok-imagine-video-1.5")
    pipeline.scenes = [new_scene(0)]
    scene = pipeline.scenes[0]
    scene.script = "A lighthouse at dusk."
    scene.image_params = {"aspect_ratio": "9:16"}
    win.movie._set_pipeline(pipeline)
    try:
        row = win.movie._rows[scene.id]
        # pre-filled from the scene, not the bare model defaults
        assert row.script_edit.toPlainText() == "A lighthouse at dusk."
        assert row.image_param_form is not None
        assert row.image_param_form.widgets["aspect_ratio"].currentText() == "9:16"
        assert row.image_param_form.widgets["image_size"].currentText() == "1K"  # model default
        assert row.video_param_form is not None
        assert row.video_param_form.widgets["duration"].value() == 6  # model default, no override yet

        row.script_edit.setPlainText("A lighthouse at dawn instead.")
        row.image_param_form.widgets["aspect_ratio"].setCurrentText("1:1")
        row.video_param_form.widgets["duration"].setValue(3)
        row._save_details()

        assert scene.script == "A lighthouse at dawn instead."
        assert scene.image_params["aspect_ratio"] == "1:1"
        assert scene.image_params["image_size"] == "1K"   # untouched default captured too
        assert scene.video_params["duration"] == 3
        # sync() must not clobber the just-saved text back to some stale value
        assert row.script_edit.toPlainText() == "A lighthouse at dawn instead."
    finally:
        win.movie._set_pipeline(MoviePipeline(name="empty"))


def test_run_image_batch_limit_queues_fewer_and_continue_finishes_the_rest(win):
    """The batch-size/continue feature: limit=N only queues the next N
    scenes still missing an image (lowest index first); calling
    run_image_batch again afterwards - with no limit, or a bigger one -
    picks up exactly where it left off, since the target filter is always
    "still missing this stage", not some separate resume cursor."""
    from prismcut.core.pipeline import MoviePipeline, new_scene

    calls = []

    class FakeAdapter:
        def generate_image(self, model_id, prompt, params, refs=None):
            calls.append(1)
            return [__file__]

    pipeline = MoviePipeline(name="Limit test", brief="brief",
                             script_model="google::gemini-3.6-flash",
                             image_model="google::gemini-3.1-flash-image",
                             video_model="xai::grok-imagine-video-1.5")
    pipeline.scenes = [new_scene(0), new_scene(1), new_scene(2)]
    win.movie._set_pipeline(pipeline)
    saved_get_adapter = win.get_adapter
    win.get_adapter = lambda provider: FakeAdapter()
    try:
        win.movie.run.run_image_batch(limit=1)
        # Wait on the actual completion signal (scene.image.active), not on
        # calls (work() running on a worker thread completes before done()
        # has marshaled back to the GUI thread and pushed the StageAsset) -
        # the same distinction the sequential-context test above relies on.
        assert _wait_until(lambda: pipeline.scenes[0].image.active is not None)
        assert sum(1 for s in pipeline.scenes if s.image.active) == 1
        assert len(calls) == 1
        assert pipeline.status != "images_ready"   # stage not fully done yet

        win.movie.run.run_image_batch()   # continue with the rest, no limit this time
        assert _wait_until(lambda: all(s.image.active for s in pipeline.scenes), timeout=10.0)
        assert len(calls) == 3
        assert pipeline.status == "images_ready"
    finally:
        win.get_adapter = saved_get_adapter
        win.movie._set_pipeline(MoviePipeline(name="empty"))


def test_movie_pipeline_stage_buttons_relabel_as_scenes_progress(win):
    """The dynamic Generate/Continue labeling that doubles as the 'continue
    where I left off' affordance: no progress -> plain 'Generate', partial
    progress -> 'Continue (N left)', full progress -> disabled checkmark."""
    from prismcut.core.pipeline import MoviePipeline, StageAsset, new_scene

    pipeline = MoviePipeline(name="Relabel test", brief="brief",
                             script_model="google::gemini-3.6-flash",
                             image_model="google::gemini-3.1-flash-image",
                             video_model="xai::grok-imagine-video-1.5")
    pipeline.scenes = [new_scene(0), new_scene(1), new_scene(2)]
    win.movie._set_pipeline(pipeline)
    try:
        assert win.movie.images_btn.text() == "🖼 Generate scene images"
        assert win.movie.images_btn.isEnabled()

        item = win.bin.add_generated(__file__, {"mode": "image"})
        pipeline.scenes[0].image.push(StageAsset(media_id=item.id, source="generated"))
        win.movie._sync_buttons()
        assert win.movie.images_btn.text() == "🖼 Continue images (2 left)"
        assert win.movie.images_btn.isEnabled()

        for s in pipeline.scenes[1:]:
            s.image.push(StageAsset(media_id=item.id, source="generated"))
        win.movie._sync_buttons()
        assert win.movie.images_btn.text() == "🖼 ✓ All scene images generated"
        assert not win.movie.images_btn.isEnabled()
    finally:
        win.movie._set_pipeline(MoviePipeline(name="empty"))


def test_batch_wording_reports_limited_vs_all_remaining(win):
    assert win.movie._batch_wording(12, None) == "all 12 remaining"
    assert win.movie._batch_wording(12, 5) == "5 of 12 remaining"
    assert win.movie._batch_wording(12, 100) == "all 12 remaining"   # limit > remaining


def test_estimate_batch_cost_sums_per_scene_with_overrides(win):
    """The new cost-estimation feature: total cost across the scenes a
    batch is about to queue, honoring each scene's own param overrides
    (video length varies scene-to-scene) rather than one flat per-scene
    number - uses the real bundled pricing data, not mocked, since this
    is exactly what a user sees in the confirm dialog."""
    from prismcut.core.pipeline import MoviePipeline, new_scene

    pipeline = MoviePipeline(name="Cost test", brief="brief",
                             script_model="google::gemini-3.6-flash",
                             image_model="google::gemini-3.1-flash-image",
                             video_model="xai::grok-imagine-video-1.5")
    s1, s2 = new_scene(0), new_scene(1)
    s2.video_params = {"duration": 10}   # longer than the model's default of 6s
    pipeline.scenes = [s1, s2]
    win.movie._set_pipeline(pipeline)
    try:
        img_cost = win.movie._estimate_batch_cost(pipeline.image_model, [s1, s2], "image")
        assert img_cost == pytest.approx(0.045 * 2)   # $0.045/image (1K) x 2 scenes

        vid_cost = win.movie._estimate_batch_cost(pipeline.video_model, [s1, s2], "video")
        assert vid_cost == pytest.approx(0.080 * 6 + 0.080 * 10)   # $0.08/s x (default 6s + overridden 10s)

        assert win.movie._estimate_batch_cost("nobody::unpriced-model", [s1], "image") is None
    finally:
        win.movie._set_pipeline(MoviePipeline(name="empty"))


def test_run_images_confirm_dialog_includes_cost_estimate(win, monkeypatch):
    import prismcut.ui.panels.movie_pipeline as movie_pipeline_mod
    from prismcut.core.pipeline import MoviePipeline, new_scene

    pipeline = MoviePipeline(name="Cost dialog test", brief="brief",
                             script_model="google::gemini-3.6-flash",
                             image_model="google::gemini-3.1-flash-image",
                             video_model="xai::grok-imagine-video-1.5")
    pipeline.scenes = [new_scene(0), new_scene(1)]
    win.movie._set_pipeline(pipeline)
    captured = {}

    def fake_confirm(parent, settings, key, title, text, ok_text):
        captured["text"] = text
        return False   # decline - run_image_batch must never actually fire

    monkeypatch.setattr(movie_pipeline_mod, "confirm_destructive", fake_confirm)
    try:
        win.movie._run_images()
        assert "est. $" in captured.get("text", "")
    finally:
        win.movie._set_pipeline(MoviePipeline(name="empty"))


def test_scene_row_shows_failure_state_and_clears_on_retry_success(win):
    """The failure-visibility + retry-after-rejection feature: a failed
    generation (moderation rejection, API constraint, etc.) sets
    scene.last_error, which the row surfaces as a distinct icon plus a
    persistent (not tooltip-only) message rather than looking merely
    "still pending" - and a subsequent successful retry clears both."""
    from prismcut.core.pipeline import MoviePipeline, new_scene
    from prismcut.ui.widgets.common import STATUS_ICONS

    class FailThenSucceedAdapter:
        def __init__(self):
            self.calls = 0

        def generate_image(self, model_id, prompt, params, refs=None):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("rejected by moderation")
            return [__file__]

    pipeline = MoviePipeline(name="Failure test", brief="brief",
                             script_model="google::gemini-3.6-flash",
                             image_model="google::gemini-3.1-flash-image",
                             video_model="xai::grok-imagine-video-1.5")
    pipeline.scenes = [new_scene(0)]
    scene = pipeline.scenes[0]
    win.movie._set_pipeline(pipeline)
    win.tabs.setCurrentWidget(win.movie)   # isVisible() below reflects real tab visibility
    adapter = FailThenSucceedAdapter()
    saved_get_adapter = win.get_adapter
    win.get_adapter = lambda provider: adapter
    try:
        win.movie.run.regenerate_scene_image(scene.id)
        assert _wait_until(lambda: bool(scene.last_error))
        assert "rejected by moderation" in scene.last_error

        row = win.movie._rows[scene.id]
        assert row.error_label.isVisible()
        assert "rejected by moderation" in row.error_label.text()
        assert row.icon.text() == STATUS_ICONS["error"]

        win.movie.run.regenerate_scene_image(scene.id)   # retry, same prompt this time succeeds
        assert _wait_until(lambda: scene.image.active is not None)
        assert scene.last_error == ""
        assert not row.error_label.isVisible()
        assert row.icon.text() != STATUS_ICONS["error"]
    finally:
        win.get_adapter = saved_get_adapter
        win.movie._set_pipeline(MoviePipeline(name="empty"))


def test_add_media_at_playhead_auto_splits_video_audio_and_undo_removes_both(win, monkeypatch,
                                                                              tmp_path):
    """The auto-split feature: a video with embedded audio landing on the
    timeline gets a companion audio clip at the same start/duration, and is
    itself marked strip_audio so the sound doesn't play twice - bundled
    into one undo step so Ctrl+Z doesn't leave an orphaned audio clip."""
    from prismcut.core import project as project_mod

    def fake_extract_audio(src, dst_ext=".m4a"):
        out = tmp_path / "extracted.m4a"
        out.write_bytes(b"fake-audio")
        return out

    monkeypatch.setattr(project_mod.media_utils, "extract_audio", fake_extract_audio)
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"fake-mp4")
    item = win.project.add_media(vid)
    item.has_audio = True
    audio_item_id = None
    try:
        before_ids = set(win.project.clips.keys())
        clip = win.timeline.add_media_at_playhead(item.id)
        assert clip is not None
        assert clip.strip_audio is True
        new_ids = set(win.project.clips.keys()) - before_ids
        assert len(new_ids) == 2   # the video clip + its auto-split audio companion

        audio_clip = next(win.project.clips[cid] for cid in new_ids if cid != clip.id)
        audio_item_id = audio_clip.media_id
        assert audio_clip.start == clip.start
        assert audio_clip.duration == clip.duration
        assert audio_clip.track_id in {t.id for t in win.project.audio_tracks()}

        win.undo_stack.undo()
        assert set(win.project.clips.keys()) == before_ids   # both removed together

        win.undo_stack.redo()
        assert set(win.project.clips.keys()) - before_ids == new_ids   # both restored together
    finally:
        win.project.remove_media(item.id)
        if audio_item_id:
            win.project.remove_media(audio_item_id)
        win.timeline.refresh(True)


def test_export_dialog_wires_success_and_failure_toasts(win, monkeypatch, tmp_path):
    """Bug fix: export used to wire zero on_done/on_fail - failure was only
    visible as a small red label buried in the Jobs dock, and success gave
    no feedback at all once the dialog (which closes itself immediately
    after submitting) was already gone."""
    import prismcut.ui.dialogs.export_dialog as export_dialog_mod
    from prismcut.ui.dialogs.export_dialog import ExportDialog

    toasts = []
    monkeypatch.setattr(win, "toast",
                        lambda text, kind="info", timeout_ms=5000: toasts.append((text, kind)))

    out = tmp_path / "out.mp4"
    dlg = ExportDialog(win.project, win.jobs, win.settings, win)
    dlg.out_edit.setText(str(out))
    monkeypatch.setattr(export_dialog_mod, "run_render", lambda project, opts, job: str(out))
    dlg._render()
    assert _wait_until(lambda: len(toasts) == 1)
    assert toasts[0][1] == "success"
    assert str(out) in toasts[0][0]

    toasts.clear()
    dlg2 = ExportDialog(win.project, win.jobs, win.settings, win)
    dlg2.out_edit.setText(str(tmp_path / "out2.mp4"))

    def fail_render(project, opts, job):
        raise RuntimeError("boom")

    monkeypatch.setattr(export_dialog_mod, "run_render", fail_render)
    dlg2._render()
    assert _wait_until(lambda: len(toasts) == 1)
    assert toasts[0][1] == "error"


def test_audio_normalize_creates_undo_entry(win, monkeypatch):
    """Bug fix: Normalize used to set clip.gain_db directly with no undo
    entry, unlike every other gain/fade edit in this panel."""
    from prismcut.ui.panels import audio_panel as audio_panel_mod

    monkeypatch.setattr(audio_panel_mod.media_utils, "measure_loudness", lambda path: -20.0)
    aud = win.project.add_media(__file__, group="audio")
    aud.kind = "audio"
    track = win.project.audio_tracks()[0]
    clip = win.project.add_clip(aud.id, track.id, 0.0, 3.0)
    clip.gain_db = 0.0
    try:
        win.audio.show_clip(clip.id)
        # count() tracks total history, not "currently applied" - it never
        # shrinks on undo() (only the stack's internal index moves), so
        # comparing raw counts is unreliable on this shared, cross-test
        # stack (a prior test's own trailing undo() can leave "redo"
        # history that a later push then truncates away). canUndo()/undo()
        # actually reverting the value is what matters.
        win.audio._normalize()
        assert _wait_until(lambda: clip.gain_db != 0.0)
        assert win.undo_stack.canUndo()

        win.undo_stack.undo()
        assert clip.gain_db == 0.0
    finally:
        win.project.remove_media(aud.id)
        win.audio.show_clip(None)


def test_effects_panel_spinbox_only_edit_creates_undo_entry(win):
    """Bug fix: _gesture_before was only primed by the slider's
    sliderPressed signal, so an edit made purely through the spinbox
    (typing a value or clicking its arrows, never touching the slider
    handle) was applied live but silently invisible to undo."""
    img = win.project.add_media(__file__)
    img.kind = "image"
    track = win.project.video_tracks()[-1]
    clip = win.project.add_clip(img.id, track.id, 0.0, 3.0)
    try:
        win.effects.show_clip(clip.id)
        brightness = win.effects.sliders["brightness"]

        # Spinbox-only edit - never touches slider.sliderPressed/sliderReleased.
        brightness.spin.setValue(42)
        brightness.spin.editingFinished.emit()

        assert clip.effects.get("brightness") == 42
        # count() tracks total history, not "currently applied" state, and
        # never shrinks on undo() - unreliable to compare on this shared,
        # cross-test stack (see test_audio_normalize_creates_undo_entry).
        assert win.undo_stack.canUndo()

        win.undo_stack.undo()
        assert clip.effects.get("brightness", 0) != 42
    finally:
        win.project.remove_media(img.id)
        win.effects.show_clip(None)


def test_effects_panel_chroma_key_toggle_creates_undo_entry(win):
    img = win.project.add_media(__file__)
    img.kind = "image"
    track = win.project.video_tracks()[-1]
    clip = win.project.add_clip(img.id, track.id, 0.0, 3.0)
    try:
        win.effects.show_clip(clip.id)

        win.effects.chroma_check.setChecked(True)

        assert clip.effects.get("chroma_key") is True
        assert clip.effects.get("chroma_key_color") == "#00ff00"
        assert win.undo_stack.canUndo()

        win.undo_stack.undo()
        assert "chroma_key" not in clip.effects
    finally:
        win.project.remove_clip(clip.id)
        win.project.remove_media(img.id)
        win.effects.show_clip(None)


def test_effects_panel_chroma_key_and_slider_effects_do_not_clobber_each_other(win):
    """Both _changed() (sliders) and _chroma_changed() (chroma key) rebuild
    clip.effects - each must only touch the keys it owns, or setting one
    kind of effect after the other would silently wipe the first."""
    img = win.project.add_media(__file__)
    img.kind = "image"
    track = win.project.video_tracks()[-1]
    clip = win.project.add_clip(img.id, track.id, 0.0, 3.0)
    try:
        win.effects.show_clip(clip.id)

        win.effects.chroma_check.setChecked(True)
        win.effects.sliders["brightness"].spin.setValue(30)
        win.effects.sliders["brightness"].spin.editingFinished.emit()

        assert clip.effects.get("chroma_key") is True
        assert clip.effects.get("brightness") == 30

        # Now change a chroma-key parameter - the slider effect must survive.
        win.effects.chroma_similarity.spin.setValue(55)
        win.effects.chroma_similarity.spin.editingFinished.emit()

        assert clip.effects.get("brightness") == 30
        assert clip.effects.get("chroma_key_similarity") == 55
    finally:
        win.project.remove_clip(clip.id)
        win.project.remove_media(img.id)
        win.effects.show_clip(None)


def test_effects_panel_show_clip_loads_chroma_key_state(win):
    img = win.project.add_media(__file__)
    img.kind = "image"
    track = win.project.video_tracks()[-1]
    a = win.project.add_clip(img.id, track.id, 0.0, 3.0)
    a.effects = {"chroma_key": True, "chroma_key_color": "#0000ff",
                "chroma_key_similarity": 35, "chroma_key_blend": 15}
    b = win.project.add_clip(img.id, track.id, 3.0, 2.0)
    try:
        win.effects.show_clip(a.id)
        assert win.effects.chroma_check.isChecked() is True
        assert win.effects.chroma_color.currentIndex() == 1   # Blue screen
        assert win.effects.chroma_similarity.value() == 35
        assert win.effects.chroma_blend.value() == 15

        win.effects.show_clip(b.id)
        assert win.effects.chroma_check.isChecked() is False
    finally:
        win.project.remove_clip(a.id)
        win.project.remove_clip(b.id)
        win.project.remove_media(img.id)
        win.effects.show_clip(None)


def test_effects_panel_reset_clears_chroma_key_too(win):
    img = win.project.add_media(__file__)
    img.kind = "image"
    track = win.project.video_tracks()[-1]
    clip = win.project.add_clip(img.id, track.id, 0.0, 3.0)
    clip.effects = {"chroma_key": True, "brightness": 40}
    try:
        win.effects.show_clip(clip.id)

        win.effects._reset()

        assert clip.effects == {}
        assert win.effects.chroma_check.isChecked() is False
    finally:
        win.project.remove_clip(clip.id)
        win.project.remove_media(img.id)
        win.effects.show_clip(None)


def test_effects_panel_load_lut_is_undoable(win, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog

    lut = tmp_path / "teal_orange.cube"
    lut.write_text("LUT_3D_SIZE 2\n", encoding="utf-8")
    img = win.project.add_media(__file__)
    img.kind = "image"
    track = win.project.video_tracks()[-1]
    clip = win.project.add_clip(img.id, track.id, 0.0, 3.0)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(lut), "*.cube"))
    try:
        win.effects.show_clip(clip.id)

        win.effects._load_lut()

        assert clip.effects.get("lut_path") == str(lut)
        assert win.effects.lut_label.text() == "teal_orange.cube"
        assert win.undo_stack.canUndo()

        win.undo_stack.undo()
        assert "lut_path" not in clip.effects
    finally:
        win.project.remove_clip(clip.id)
        win.project.remove_media(img.id)
        win.effects.show_clip(None)


def test_effects_panel_load_lut_cancelled_does_nothing(win, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    img = win.project.add_media(__file__)
    img.kind = "image"
    track = win.project.video_tracks()[-1]
    clip = win.project.add_clip(img.id, track.id, 0.0, 3.0)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""))
    try:
        win.effects.show_clip(clip.id)
        win.effects._load_lut()
        assert "lut_path" not in clip.effects
    finally:
        win.project.remove_clip(clip.id)
        win.project.remove_media(img.id)
        win.effects.show_clip(None)


def test_effects_panel_clear_lut_is_undoable(win):
    img = win.project.add_media(__file__)
    img.kind = "image"
    track = win.project.video_tracks()[-1]
    clip = win.project.add_clip(img.id, track.id, 0.0, 3.0)
    clip.effects = {"lut_path": "/some/look.cube"}
    try:
        win.effects.show_clip(clip.id)

        win.effects._clear_lut()

        assert "lut_path" not in clip.effects
        assert win.effects.lut_label.text() == "No LUT"
        assert win.undo_stack.canUndo()

        win.undo_stack.undo()
        assert clip.effects.get("lut_path") == "/some/look.cube"
    finally:
        win.project.remove_clip(clip.id)
        win.project.remove_media(img.id)
        win.effects.show_clip(None)


def test_effects_panel_clear_lut_without_a_lut_is_a_noop(win):
    img = win.project.add_media(__file__)
    img.kind = "image"
    track = win.project.video_tracks()[-1]
    clip = win.project.add_clip(img.id, track.id, 0.0, 3.0)
    try:
        win.effects.show_clip(clip.id)
        win.effects._clear_lut()   # no lut_path set - must not crash or push an entry
        assert clip.effects == {}
    finally:
        win.project.remove_clip(clip.id)
        win.project.remove_media(img.id)
        win.effects.show_clip(None)


def test_effects_panel_show_clip_updates_lut_label(win):
    img = win.project.add_media(__file__)
    img.kind = "image"
    track = win.project.video_tracks()[-1]
    a = win.project.add_clip(img.id, track.id, 0.0, 3.0)
    a.effects = {"lut_path": "/luts/moody.cube"}
    b = win.project.add_clip(img.id, track.id, 3.0, 2.0)
    try:
        win.effects.show_clip(a.id)
        assert win.effects.lut_label.text() == "moody.cube"

        win.effects.show_clip(b.id)
        assert win.effects.lut_label.text() == "No LUT"
    finally:
        win.project.remove_clip(a.id)
        win.project.remove_clip(b.id)
        win.project.remove_media(img.id)
        win.effects.show_clip(None)


def test_effects_panel_reset_clears_lut_too(win):
    img = win.project.add_media(__file__)
    img.kind = "image"
    track = win.project.video_tracks()[-1]
    clip = win.project.add_clip(img.id, track.id, 0.0, 3.0)
    clip.effects = {"lut_path": "/luts/moody.cube", "brightness": 40}
    try:
        win.effects.show_clip(clip.id)

        win.effects._reset()

        assert clip.effects == {}
        assert win.effects.lut_label.text() == "No LUT"
    finally:
        win.project.remove_clip(clip.id)
        win.project.remove_media(img.id)
        win.effects.show_clip(None)


def test_timeline_add_track_button_is_undoable(win):
    """Bug fix: the Timeline panel's own toolbar/context-menu 'Add track'
    controls used to call project.add_track() directly with no undo entry,
    while the identical action from the app menu bar (MainWindow.add_track,
    wired in here as TimelineWidget.on_add_track) was already correctly
    undoable - an inconsistent, silently non-reversible action depending
    only on which control you clicked."""
    before_ids = {t.id for t in win.project.tracks}
    win.timeline._add_track("video")
    after_ids = {t.id for t in win.project.tracks}
    new_ids = after_ids - before_ids
    assert len(new_ids) == 1

    win.undo_stack.undo()
    assert {t.id for t in win.project.tracks} == before_ids

    win.undo_stack.redo()
    assert {t.id for t in win.project.tracks} - before_ids == new_ids

    win.undo_stack.undo()   # leave shared win.project state clean for later tests
    assert {t.id for t in win.project.tracks} == before_ids


def test_track_header_mute_toggle_is_undoable(win):
    """Bug fix: TrackHeader's M/S buttons used to setattr(track, ...)
    directly with no undo entry, unlike the identical action from the
    Audio Lab mixer (TrackStrip._toggle_attr), which was already correct."""
    from PySide6.QtWidgets import QToolButton

    from prismcut.ui.panels.timeline import TrackHeader

    track = win.project.video_tracks()[-1]
    assert track.mute is False
    header = TrackHeader(track, win.timeline)
    mute_btn = next(b for b in header.findChildren(QToolButton) if b.text() == "M")

    mute_btn.setChecked(True)
    assert track.mute is True

    win.undo_stack.undo()
    assert track.mute is False

    win.undo_stack.redo()
    assert track.mute is True

    win.undo_stack.undo()   # leave shared win.project state clean for later tests
    assert track.mute is False


def test_timeline_marker_keyboard_shortcut_adds_marker_and_is_undoable(win):
    """"M" adds a marker at the playhead, mirroring how the razor tool binds
    "R" - and like every other timeline mutation, it must be undoable."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    win.timeline.set_playhead(3.0)
    before = len(win.project.markers)

    ev = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_M, Qt.KeyboardModifier.NoModifier)
    win.timeline.keyPressEvent(ev)

    assert len(win.project.markers) == before + 1
    marker = win.project.markers[-1]
    assert marker.time == 3.0

    win.undo_stack.undo()
    assert len(win.project.markers) == before

    win.undo_stack.redo()
    assert len(win.project.markers) == before + 1

    win.undo_stack.undo()   # leave shared win.project state clean for later tests
    assert len(win.project.markers) == before


def test_timeline_remove_marker_is_undoable(win):
    # Added directly (not through the undo stack) so this test only has to
    # verify remove_marker() itself is undoable, same spirit as the
    # add-directly-then-mutate-through-timeline setup used for rename/recolor
    # below.
    marker = win.project.add_marker(5.0, "temp")

    win.timeline.remove_marker(marker.id)
    assert marker not in win.project.markers
    assert win.undo_stack.canUndo()

    win.undo_stack.undo()
    assert marker in win.project.markers

    win.undo_stack.redo()   # back to removed - matches the pre-test baseline
    assert marker not in win.project.markers


def test_timeline_rename_marker_is_undoable(win):
    marker = win.project.add_marker(5.0, "old label")
    try:
        win.timeline.rename_marker(marker.id, "new label")
        assert marker.label == "new label"

        win.undo_stack.undo()
        assert marker.label == "old label"

        win.undo_stack.redo()
        assert marker.label == "new label"
    finally:
        win.project.remove_marker(marker.id)   # leave shared win.project state clean


def test_timeline_recolor_marker_is_undoable(win):
    marker = win.project.add_marker(5.0, color="#e8a33d")
    try:
        win.timeline.recolor_marker(marker.id, "#42a5f5")
        assert marker.color == "#42a5f5"

        win.undo_stack.undo()
        assert marker.color == "#e8a33d"
    finally:
        win.project.remove_marker(marker.id)   # leave shared win.project state clean


def test_timeline_remove_marker_unknown_id_is_a_noop(win):
    before = list(win.project.markers)
    win.timeline.remove_marker("does-not-exist")
    assert win.project.markers == before


def test_ruler_time_at_reads_context_menu_event_position(win):
    """QContextMenuEvent (unlike QMouseEvent) only exposes pos()/QPoint in
    Qt6, not position()/QPointF - calling the wrong one raises
    AttributeError the instant a user right-clicks the ruler. Exercises the
    real event class (menu.exec() itself is not exercised here since it's
    a blocking modal call under a real Qt event loop, same reason no other
    contextMenuEvent in this codebase is smoke-tested end-to-end)."""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QContextMenuEvent

    win.timeline.pps = 26.0
    win.timeline.view.horizontalScrollBar().setValue(0)
    ev = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(130, 10), QPoint(130, 10))

    assert win.timeline.ruler._time_at(ev) == pytest.approx(5.0)


def test_timeline_snap_time_snaps_to_marker(win):
    """_snap_points() was extended to include marker positions, closing the
    existing gap where dragging/trimming clips could never align to a
    marker the way it already could to another clip's edge or the
    playhead."""
    marker = win.project.add_marker(7.0, "snap target")
    try:
        assert win.timeline.snap_time(7.2) == 7.0
    finally:
        win.project.remove_marker(marker.id)


def test_timeline_close_gap_after_is_undoable(win):
    img = win.project.add_media(__file__)
    img.kind = "image"
    track = win.project.video_tracks()[-1]
    a = win.project.add_clip(img.id, track.id, 0.0, 3.0)
    b = win.project.add_clip(img.id, track.id, 5.0, 2.0)
    try:
        win.timeline.close_gap_after(track.id, 4.0)
        assert b.start == 3.0
        assert win.undo_stack.canUndo()

        win.undo_stack.undo()
        assert b.start == 5.0

        win.undo_stack.redo()
        assert b.start == 3.0
    finally:
        win.project.remove_clip(a.id)
        win.project.remove_clip(b.id)
        win.project.remove_media(img.id)


def test_timeline_close_gap_after_is_a_noop_without_a_gap(win):
    img = win.project.add_media(__file__)
    img.kind = "image"
    track = win.project.video_tracks()[-1]
    a = win.project.add_clip(img.id, track.id, 0.0, 3.0)
    try:
        win.timeline.close_gap_after(track.id, 1.0)   # inside the clip, not a gap
        assert a.start == 0.0
    finally:
        win.project.remove_clip(a.id)
        win.project.remove_media(img.id)


def test_set_transition_is_undoable(win):
    img = win.project.add_media(__file__)
    img.kind = "image"
    track = win.project.video_tracks()[-1]
    a = win.project.add_clip(img.id, track.id, 0.0, 3.0)
    b = win.project.add_clip(img.id, track.id, 3.0, 2.0)
    try:
        win.timeline.set_transition(a.id, 1.0)
        assert a.transition_out == 1.0
        assert win.undo_stack.canUndo()

        win.undo_stack.undo()
        assert a.transition_out == 0.0

        win.undo_stack.redo()
        assert a.transition_out == 1.0
    finally:
        win.project.remove_clip(a.id)
        win.project.remove_clip(b.id)
        win.project.remove_media(img.id)


def test_add_transition_dialog_prompts_and_clamps_to_shorter_clip(win, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    img = win.project.add_media(__file__)
    img.kind = "image"
    track = win.project.video_tracks()[-1]
    a = win.project.add_clip(img.id, track.id, 0.0, 3.0)
    b = win.project.add_clip(img.id, track.id, 3.0, 1.0)   # shorter clip caps the prompt's max
    captured = {}

    def fake_get_double(parent, title, label, value, min_value, max_value, decimals):
        captured["max"] = max_value
        return 0.8, True

    monkeypatch.setattr(QInputDialog, "getDouble", fake_get_double)
    try:
        win.timeline.add_transition_dialog(a.id)

        assert captured["max"] == pytest.approx(0.95)   # min(3.0, 1.0) - 0.05
        assert a.transition_out == 0.8
    finally:
        win.project.remove_clip(a.id)
        win.project.remove_clip(b.id)
        win.project.remove_media(img.id)


def test_add_transition_dialog_cancelled_leaves_transition_unset(win, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    img = win.project.add_media(__file__)
    img.kind = "image"
    track = win.project.video_tracks()[-1]
    a = win.project.add_clip(img.id, track.id, 0.0, 3.0)
    b = win.project.add_clip(img.id, track.id, 3.0, 2.0)
    monkeypatch.setattr(QInputDialog, "getDouble", lambda *a, **k: (0.5, False))
    try:
        win.timeline.add_transition_dialog(a.id)
        assert a.transition_out == 0.0
    finally:
        win.project.remove_clip(a.id)
        win.project.remove_clip(b.id)
        win.project.remove_media(img.id)


def test_add_transition_dialog_noop_without_a_next_clip(win):
    img = win.project.add_media(__file__)
    img.kind = "image"
    track = win.project.video_tracks()[-1]
    a = win.project.add_clip(img.id, track.id, 0.0, 3.0)
    try:
        win.timeline.add_transition_dialog(a.id)   # no next clip - must not crash or prompt
        assert a.transition_out == 0.0
    finally:
        win.project.remove_clip(a.id)
        win.project.remove_media(img.id)


def test_track_at_position_finds_the_right_row(win):
    from prismcut.ui.panels.timeline import AUDIO_H, TRACK_H

    y = 0.0
    for tr in win.timeline.ordered_tracks():
        found = win.timeline.track_at_position(y + 1.0)
        assert found is not None and found.id == tr.id
        y += TRACK_H if tr.kind == "video" else AUDIO_H


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
        def generate_image(self, model_id, prompt, params, refs=None):
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


def test_refresh_pricing_wired_into_help_menu_and_shows_toast(win, monkeypatch):
    import prismcut.core.pricing as pricing_mod

    assert callable(win._refresh_pricing)
    assert callable(win._maybe_refresh_pricing_on_startup)

    monkeypatch.setattr(pricing_mod, "fetch_remote",
                        lambda: {"acme::x": {"unit": "per_image", "amount": 1.0}})
    toasts = []
    monkeypatch.setattr(win, "toast",
                        lambda text, kind="info", timeout_ms=5000: toasts.append((text, kind)))
    win._refresh_pricing(silent=False)
    assert _wait_until(lambda: len(toasts) == 1)
    assert toasts[0][1] == "success"


def test_maybe_refresh_pricing_on_startup_respects_should_refresh(win, monkeypatch):
    import prismcut.ui.main_window as main_window_mod

    calls = []
    monkeypatch.setattr(main_window_mod.pricing, "should_refresh", lambda settings: False)
    monkeypatch.setattr(win, "_refresh_pricing", lambda silent: calls.append(silent))
    win._maybe_refresh_pricing_on_startup()
    assert calls == []

    monkeypatch.setattr(main_window_mod.pricing, "should_refresh", lambda settings: True)
    monkeypatch.setattr(main_window_mod.pricing, "mark_refreshed", lambda settings: None)
    win._maybe_refresh_pricing_on_startup()
    assert calls == [True]


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


# -------------------------------------------------------------------- titles

def test_title_dialog_result_media_creates_title_item(win):
    from prismcut.ui.dialogs.title_dialog import POSITIONS, TITLE_COLORS, TitleDialog

    dlg = TitleDialog(win.project, win)
    item = None
    try:
        dlg.text_edit.setText("Chapter One")
        dlg.size_spin.setValue(48)
        dlg.color_combo.setCurrentIndex(2)      # Amber
        dlg.position_combo.setCurrentIndex(1)   # Top
        dlg.bold_check.setChecked(False)
        dlg.shadow_check.setChecked(False)
        dlg.duration_spin.setValue(8)

        item = dlg.result_media()

        assert item.kind == "title"
        assert item.id in win.project.media
        assert item.meta["title_text"] == "Chapter One"
        assert item.meta["font_size"] == 48
        assert item.meta["color"] == TITLE_COLORS[2][1]
        assert item.meta["position"] == POSITIONS[1][1]
        assert item.meta["bold"] is False
        assert item.meta["shadow"] is False
        assert item.duration == 8.0
    finally:
        if item:
            win.project.media.pop(item.id, None)
        dlg.close()


def test_title_dialog_blank_text_falls_back_to_default_label(win):
    from prismcut.ui.dialogs.title_dialog import TitleDialog

    dlg = TitleDialog(win.project, win)
    item = None
    try:
        item = dlg.result_media()   # text_edit left empty
        assert item.label == "Title"
        assert item.meta["title_text"] == "Title"
    finally:
        if item:
            win.project.media.pop(item.id, None)
        dlg.close()


def test_project_bin_add_title_media_is_undoable(win):
    item = win.project.add_title("Undo me")

    win.bin.add_title_media(item)
    assert win.undo_stack.canUndo()

    win.undo_stack.undo()
    assert item.id not in win.project.media

    win.undo_stack.redo()
    assert item.id in win.project.media


def test_monitor_show_title_renders_without_crashing(win):
    win.clip_monitor.show_title({
        "title_text": "Preview", "font_size": 64, "color": "#ffffff",
        "position": "center", "bold": True, "shadow": True,
    }, canvas_height=1080)

    assert win.clip_monitor.stack.currentWidget() is win.clip_monitor.image_label
    assert win.clip_monitor._pix is not None and not win.clip_monitor._pix.isNull()


def test_project_monitor_preview_at_title_clip(win):
    # A far-away, deliberately weird start time - win.project is a shared,
    # module-scoped fixture other tests leave clips on, and resolve_at()
    # (which preview_at() is built on) would silently resolve to one of
    # those instead of this test's own clip if the ranges overlapped.
    start = 100_000.0
    item = win.project.add_title("On Timeline", duration=3.0)
    track = win.project.video_tracks()[0]   # topmost - guaranteed to win resolve_at()
    clip = win.project.add_clip(item.id, track.id, start, 3.0)
    try:
        win.project_monitor.preview_at(start + 1.0)

        assert win.project_monitor._preview_media_id == item.id
        assert win.project_monitor.stack.currentWidget() is win.project_monitor.image_label
    finally:
        win.project.remove_clip(clip.id)
        win.project.media.pop(item.id, None)
        win.project_monitor.preview_at(0.0)   # reset shared monitor state for later tests


# ----------------------------------------------------------------- captions

def test_generate_captions_opens_dialog_with_segments(win, monkeypatch, tmp_path):
    from prismcut.core.captions import Segment

    audio = tmp_path / "narration.mp3"
    audio.write_bytes(b"fake-mp3")
    item = win.bin.add_generated(str(audio), {"mode": "audio"})

    class FakeAdapter:
        def transcribe_segments(self, model_id, path):
            return [Segment(0.0, 1.0, "Hello"), Segment(1.0, 2.0, "World")]

    saved_get_adapter = win.get_adapter
    win.get_adapter = lambda provider: FakeAdapter()
    opened = {}

    def fake_exec(dlg_self):
        opened["dlg"] = dlg_self
        return 0

    from prismcut.ui.dialogs.captions_dialog import CaptionsDialog
    monkeypatch.setattr(CaptionsDialog, "exec", fake_exec)
    try:
        win._generate_captions(item.id)
        assert _wait_until(lambda: "dlg" in opened)
        text = opened["dlg"].text_edit.toPlainText()
        assert "Hello" in text and "World" in text
        assert "00:00:00,000 --> 00:00:01,000" in text
    finally:
        win.get_adapter = saved_get_adapter


def test_generate_captions_toasts_when_no_speech_detected(win, monkeypatch, tmp_path):
    audio = tmp_path / "silent.mp3"
    audio.write_bytes(b"fake-mp3")
    item = win.bin.add_generated(str(audio), {"mode": "audio"})

    class EmptyAdapter:
        def transcribe_segments(self, model_id, path):
            return []

    toasts = []
    monkeypatch.setattr(win, "toast",
                        lambda text, kind="info", timeout_ms=5000: toasts.append((text, kind)))
    saved_get_adapter = win.get_adapter
    win.get_adapter = lambda provider: EmptyAdapter()
    try:
        win._generate_captions(item.id)
        assert _wait_until(lambda: len(toasts) == 1)
        assert toasts[0][1] == "info"
    finally:
        win.get_adapter = saved_get_adapter


def test_generate_captions_toasts_on_job_failure(win, monkeypatch, tmp_path):
    audio = tmp_path / "narration.mp3"
    audio.write_bytes(b"fake-mp3")
    item = win.bin.add_generated(str(audio), {"mode": "audio"})

    class FailingAdapter:
        def transcribe_segments(self, model_id, path):
            raise RuntimeError("boom")

    toasts = []
    monkeypatch.setattr(win, "toast",
                        lambda text, kind="info", timeout_ms=5000: toasts.append((text, kind)))
    saved_get_adapter = win.get_adapter
    win.get_adapter = lambda provider: FailingAdapter()
    try:
        win._generate_captions(item.id)
        assert _wait_until(lambda: len(toasts) == 1)
        assert toasts[0][1] == "error"
    finally:
        win.get_adapter = saved_get_adapter


def test_captions_dialog_save_writes_the_edited_text(win, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog

    from prismcut.core.captions import Segment
    from prismcut.ui.dialogs.captions_dialog import CaptionsDialog

    dlg = CaptionsDialog([Segment(0.0, 1.0, "Original")], tmp_path / "default.srt", win)
    out = tmp_path / "edited.srt"
    edited = "1\n00:00:00,000 --> 00:00:01,000\nEdited text\n"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), "*.srt"))
    try:
        dlg.text_edit.setPlainText(edited)
        dlg._save()

        assert out.read_text(encoding="utf-8") == edited
    finally:
        dlg.close()


def test_captions_dialog_save_cancelled_writes_nothing(win, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog

    from prismcut.core.captions import Segment
    from prismcut.ui.dialogs.captions_dialog import CaptionsDialog

    dlg = CaptionsDialog([Segment(0.0, 1.0, "Original")], tmp_path / "default.srt", win)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    try:
        dlg._save()   # user cancelled the file dialog
        assert not list(tmp_path.glob("*.srt"))
    finally:
        dlg.close()
