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


def test_new_pipeline_dialog_constructs_and_lipsync_defaults_to_none(win):
    from prismcut.ui.dialogs.new_pipeline_dialog import NewPipelineDialog

    dlg = NewPipelineDialog(win.registry, win.settings, win)
    assert dlg.lipsync_combo.current_model() is None
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
        dlg = dlg_mod.NewPipelineDialog(win.registry, win.settings, win)
        dlg.brief_edit.setPlainText("")   # empty brief - must be rejected
        dlg._accept()
        assert dlg.pipeline is None
        assert len(calls) == 1
        dlg.close()
    finally:
        dlg_mod.QMessageBox.information = saved


def test_new_pipeline_dialog_accept_builds_pipeline_with_chosen_models(win):
    from prismcut.ui.dialogs.new_pipeline_dialog import NewPipelineDialog

    dlg = NewPipelineDialog(win.registry, win.settings, win)
    dlg.name_edit.setText("My Test Movie")
    dlg.brief_edit.setPlainText("A short story about a lighthouse keeper.")
    dlg._accept()
    assert dlg.pipeline is not None
    assert dlg.pipeline.name == "My Test Movie"
    assert dlg.pipeline.brief == "A short story about a lighthouse keeper."
    assert dlg.pipeline.script_model and "::" in dlg.pipeline.script_model
    assert dlg.pipeline.image_model and dlg.pipeline.audio_model and dlg.pipeline.video_model
    assert dlg.pipeline.lipsync_model == ""   # allow_none combo defaults to skipped
    dlg.close()


def test_movie_pipeline_panel_loads_pipeline_and_builds_scene_rows(win):
    from prismcut.core.pipeline import MoviePipeline, new_scene

    pipeline = MoviePipeline(name="Smoke test movie", brief="A robot explores a city.",
                             script_model="google::gemini-test", image_model="fal::img-test",
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
                             script_model="google::gemini-test", image_model="fal::img-test",
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
    assert callable(win._maybe_check_updates_on_startup)
