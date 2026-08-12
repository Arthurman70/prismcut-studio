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
