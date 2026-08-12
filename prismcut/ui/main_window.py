"""Main window - Kdenlive-inspired layout:

┌────────────┬──────────────────────────────┬─────────────┐
│ Project Bin│  Clip Monitor │ Proj Monitor │ Chat / Gen  │
│ Effects    ├──────────────────────────────┤ Prompt Lab  │
│            │  Timeline (V2 V1 A1 A2)      │             │
├────────────┴──────────────────────────────┴─────────────┤
│ Jobs                                                     │
└──────────────────────────────────────────────────────────┘
Central tabs: Edit · Photo Studio · Audio Lab
"""
from __future__ import annotations

from pathlib import Path

import json
import os
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QUndoStack
from PySide6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QLabel,
                               QMainWindow, QMessageBox, QSplitter, QTabWidget,
                               QUndoView, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from ..core import media as media_utils
from ..core import paths
from ..core import shortcuts
from ..core import updater
from ..core.agent import AgentToolRunner
from ..core.jobs import JobManager
from ..core.project import PROJECT_EXT, Project
from ..core.registry import Registry
from ..core.settings import Settings
from ..core.undo_commands import AddOrRemoveItemCommand
from ..providers import make_adapter
from . import theme
from .dialogs.export_dialog import ExportDialog
from .dialogs.keys_dialog import KeysDialog
from .dialogs.model_manager import ModelManagerDialog
from .dialogs.shortcuts_dialog import ShortcutsDialog
from .dialogs.update_dialog import UpdateDialog
from .panels.audio_panel import AudioPanel
from .panels.chat_panel import ChatPanel
from .panels.effects_panel import EffectsPanel
from .panels.generate_panel import GeneratePanel
from .panels.jobs_panel import JobsPanel
from .panels.monitor import ClipMonitor, ProjectMonitor
from .panels.movie_pipeline import MoviePipelinePanel
from .panels.photo_studio import PhotoStudio
from .panels.project_bin import ProjectBin
from .panels.prompt_lab import PromptLab
from .panels.timeline import TimelineWidget
from .widgets.common import ToastOverlay


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1680, 980)

        self.settings = Settings()
        self.registry = Registry()
        self.jobs = JobManager()
        self.project = Project()
        self._adapters: dict[str, object] = {}
        # One stack for the app's lifetime - never replaced, only .clear()'d
        # on project switch, so QUndoView/menu actions stay bound to it.
        self.undo_stack = QUndoStack(self)

        # ------------------------------------------------------------ panels
        self.bin = ProjectBin(self.project, self.undo_stack)
        self.clip_monitor = ClipMonitor()
        self.project_monitor = ProjectMonitor(self.project)
        self.timeline = TimelineWidget(self.project, self.undo_stack)
        self.effects = EffectsPanel(self.project, self.undo_stack)
        # Constructed after bin/timeline/effects (its tool handlers reach
        # into them) but its own __init__ only stores `self`, so it's safe
        # even though later panels (audio, etc.) don't exist quite yet.
        self.agent = AgentToolRunner(self)
        self.chat = ChatPanel(self.registry, self.settings, self.get_adapter, self.agent)
        self.generate = GeneratePanel(self.registry, self.settings, self.jobs,
                                      self.get_adapter)
        self.prompt_lab = PromptLab(self.settings)
        self.photo = PhotoStudio(self.registry, self.settings, self.jobs, self.get_adapter)
        self.audio = AudioPanel(self.project, self.registry, self.settings, self.jobs,
                                self.get_adapter, self.undo_stack)
        self.jobs_panel = JobsPanel(self.jobs, self.settings)
        # Constructed after jobs/bin/timeline exist - it only stores `self`
        # at init time (same pragmatic shape as AgentToolRunner above) and
        # reaches back into them lazily, only once a movie is actually
        # created/loaded.
        self.movie = MoviePipelinePanel(self)

        # ------------------------------------------------------------ centre
        edit_tab = QWidget()
        ev = QVBoxLayout(edit_tab)
        ev.setContentsMargins(2, 2, 2, 2)
        vsplit = QSplitter(Qt.Orientation.Vertical)
        monitors = QSplitter(Qt.Orientation.Horizontal)
        monitors.addWidget(self.clip_monitor)
        monitors.addWidget(self.project_monitor)
        vsplit.addWidget(monitors)
        vsplit.addWidget(self.timeline)
        vsplit.setSizes([420, 420])
        ev.addWidget(vsplit)

        self.tabs = QTabWidget()
        self.tabs.addTab(edit_tab, "🎬  Edit")
        self.tabs.addTab(self.photo, "🎨  Photo Studio")
        self.tabs.addTab(self.audio, "🎚  Audio Lab")
        self.tabs.addTab(self.movie, "🎞️  Movie Pipeline")
        self.setCentralWidget(self.tabs)

        # ------------------------------------------------------------ docks
        self.bin_dock = self._dock("Project Bin", self.bin,
                                   Qt.DockWidgetArea.LeftDockWidgetArea)
        self.fx_dock = self._dock("Effects", self.effects,
                                  Qt.DockWidgetArea.LeftDockWidgetArea)
        self.history_dock = self._dock("History", QUndoView(self.undo_stack),
                                       Qt.DockWidgetArea.LeftDockWidgetArea)
        self.tabifyDockWidget(self.bin_dock, self.fx_dock)
        self.tabifyDockWidget(self.fx_dock, self.history_dock)
        self.bin_dock.raise_()

        self.chat_dock = self._dock("AI Chat", self.chat,
                                    Qt.DockWidgetArea.RightDockWidgetArea)
        self.gen_dock = self._dock("Generate", self.generate,
                                   Qt.DockWidgetArea.RightDockWidgetArea)
        self.lab_dock = self._dock("Prompt Lab", self.prompt_lab,
                                   Qt.DockWidgetArea.RightDockWidgetArea)
        self.tabifyDockWidget(self.chat_dock, self.gen_dock)
        self.tabifyDockWidget(self.gen_dock, self.lab_dock)
        self.chat_dock.raise_()

        self.jobs_dock = self._dock("Jobs", self.jobs_panel,
                                    Qt.DockWidgetArea.BottomDockWidgetArea)
        self.jobs_dock.setMaximumHeight(190)

        self.resizeDocks([self.bin_dock], [290], Qt.Orientation.Horizontal)
        self.resizeDocks([self.chat_dock], [360], Qt.Orientation.Horizontal)

        self._toast_overlay = ToastOverlay(self)
        self._fullscreen_hidden_docks: list[QDockWidget] = []

        self._save_label = QLabel()
        self.statusBar().addPermanentWidget(self._save_label)
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(30_000)
        self._autosave_timer.timeout.connect(self._autosave_tick)
        self._autosave_timer.start()

        self._wire()
        self._build_menus()
        self._sync_status()
        self._check_autosave_recovery(None)
        self._restore_layout()
        # Deferred so it never competes with initial window paint - the 24h
        # throttle inside _maybe_check_updates_on_startup means this is a
        # no-op most launches anyway. Skipped entirely under the test sandbox
        # (same PRISMCUT_DATA_DIR override core.paths/core.settings already
        # honor) - a real, uncontrolled network call firing mid-test-suite
        # was previously racing process/window teardown and crashing a
        # background QThreadPool worker (core/jobs.py's "Signal source has
        # been deleted"), independent of whatever test happened to be
        # running at the time.
        if not os.environ.get("PRISMCUT_DATA_DIR"):
            QTimer.singleShot(1500, self._maybe_check_updates_on_startup)

    # ---------------------------------------------------------------- helpers
    def _dock(self, title: str, widget: QWidget, area) -> QDockWidget:
        d = QDockWidget(title, self)
        d.setWidget(widget)
        d.setObjectName(title.replace(" ", "_"))
        self.addDockWidget(area, d)
        return d

    def toast(self, text: str, kind: str = "info", timeout_ms: int = 5000):
        """Non-blocking notification - the app-wide replacement for
        one-shot statusBar() messages. kind: 'info'|'success'|'error'."""
        self._toast_overlay.show_toast(text, kind, timeout_ms)

    def get_adapter(self, provider: str):
        if provider not in self._adapters:
            self._adapters[provider] = make_adapter(provider, self.settings, self.registry)
        return self._adapters[provider]

    # ---------------------------------------------------------------- wiring
    def _wire(self):
        # bin -> monitors / timeline / studio / chat / generate
        self.bin.mediaActivated.connect(self._preview_media)
        self.bin.addToTimeline.connect(self.timeline.add_media_at_playhead)
        self.bin.openInPhotoStudio.connect(self._open_in_studio)
        self.bin.sendToChat.connect(self._media_to_chat)
        self.bin.useAsReference.connect(self._media_to_reference)
        self.bin.transcribeRequested.connect(self._transcribe_media)
        # media removal cascades to clips (Project.remove_media) - keep the
        # timeline's visuals in sync rather than showing stale clip items
        self.bin.binChanged.connect(lambda: self.timeline.refresh())
        # timeline
        self.timeline.playheadMoved.connect(self.project_monitor.preview_at)
        self.timeline.selectionChanged.connect(self._clip_selected)
        self.timeline.effectsRequested.connect(self._show_effects_for)
        self.timeline.timelineChanged.connect(self._sync_status)
        self.timeline.regenerateSceneRequested.connect(self._regenerate_pipeline_scene)
        # generate / audio results -> bin
        self.generate.resultReady.connect(self._generated)
        self.audio.resultReady.connect(self._generated)
        # photo studio
        self.photo.sendToBin.connect(lambda p, meta: self._generated(p, meta))
        self.photo.useAsReference.connect(self._path_to_reference)
        self.photo.sendToChat.connect(self._path_to_chat)
        # prompt lab -> generate
        self.prompt_lab.sendToGenerate.connect(self._lab_to_generate)
        # audio mixer changes re-render timeline headers
        self.audio.timelineChanged.connect(lambda: self.timeline.refresh())
        self.audio.mediaDropped.connect(self.bin.add_files)
        # transient feedback -> toast (non-blocking; replaces one-shot statusBar messages)
        for src in (self.photo.status, self.generate.status, self.audio.status,
                    self.chat.status, self.prompt_lab.status, self.bin.status,
                    self.movie.status):
            src.connect(lambda msg: self.toast(msg))
        # scene rows only show a status icon - clicking one raises the Jobs
        # dock, same "raise the dock that has the real detail" idiom as
        # _show_effects_for does for clip selection.
        self.movie.jobsRequested.connect(lambda: (self.jobs_dock.show(), self.jobs_dock.raise_()))
        self.jobs.jobsChanged.connect(self._sync_status)

    # ---------------------------------------------------------------- menus
    def _build_menus(self):
        mb = self.menuBar()

        def act(menu, text, fn, shortcut=None, category="General"):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
                shortcuts.register(category, text, shortcut)
            a.triggered.connect(fn)
            menu.addAction(a)
            return a

        m_file = mb.addMenu("&File")
        act(m_file, "🆕 New project", self.new_project, "Ctrl+N", "File")
        act(m_file, "📂 Open project…", self.open_project, "Ctrl+O", "File")
        self.m_recent = m_file.addMenu("🕘 Recent Projects")
        self.m_recent.aboutToShow.connect(self._rebuild_recent_menu)
        act(m_file, "💾 Save project", self.save_project, "Ctrl+S", "File")
        act(m_file, "Save project as…", lambda: self.save_project(True))
        m_file.addSeparator()
        act(m_file, "📥 Import media…", self.bin.import_dialog, "Ctrl+I", "File")
        act(m_file, "🎬 Export / render…", self.export_dialog, "Ctrl+E", "File")
        m_file.addSeparator()
        act(m_file, "Quit", self.close, "Ctrl+Q", "File")

        m_edit = mb.addMenu("&Edit")
        undo_act = self.undo_stack.createUndoAction(self, "Undo")
        undo_act.setShortcut(QKeySequence.StandardKey.Undo)
        redo_act = self.undo_stack.createRedoAction(self, "Redo")
        redo_act.setShortcut(QKeySequence.StandardKey.Redo)
        shortcuts.register("Edit", "Undo", undo_act.shortcut().toString())
        shortcuts.register("Edit", "Redo", redo_act.shortcut().toString())
        m_edit.addAction(undo_act)
        m_edit.addAction(redo_act)
        m_edit.addSeparator()
        m_edit.addAction(self.history_dock.toggleViewAction())

        m_tl = mb.addMenu("&Timeline")
        act(m_tl, "✂ Razor selected at playhead", self._razor_selected, "R", "Timeline")
        act(m_tl, "🗑 Delete selected clip", self.timeline.delete_selected, "Del", "Timeline")
        act(m_tl, "＋ Add video track", lambda: self.add_track("video"))
        act(m_tl, "＋ Add audio track", lambda: self.add_track("audio"))
        shortcuts.register("Timeline", "Zoom in", "Ctrl++ / Ctrl+wheel up")
        shortcuts.register("Timeline", "Zoom out", "Ctrl+- / Ctrl+wheel down")
        shortcuts.register("Timeline", "Nudge playhead", "Left / Right arrow")
        shortcuts.register("Timeline", "Play/pause preview", "Space")
        shortcuts.register("Project Bin", "Select all", "Ctrl+A")
        shortcuts.register("Project Bin", "Remove selected", "Del")

        m_ai = mb.addMenu("&AI")
        act(m_ai, "🔑 API Keys…", self.keys_dialog, "Ctrl+K", "AI")
        act(m_ai, "🗂 Model Manager…", self.model_manager, "Ctrl+M", "AI")
        m_ai.addSeparator()
        act(m_ai, "💬 Chat panel", lambda: (self.chat_dock.show(), self.chat_dock.raise_()))
        act(m_ai, "🚀 Generate panel", lambda: (self.gen_dock.show(), self.gen_dock.raise_()))
        act(m_ai, "🧪 Prompt Lab", lambda: (self.lab_dock.show(), self.lab_dock.raise_()))

        m_view = mb.addMenu("&View")
        for dock in (self.bin_dock, self.fx_dock, self.history_dock, self.chat_dock,
                     self.gen_dock, self.lab_dock, self.jobs_dock):
            m_view.addAction(dock.toggleViewAction())
        m_view.addSeparator()
        act(m_view, "⛶ Toggle fullscreen", self._toggle_fullscreen, "F11", "View")

        m_theme = m_view.addMenu("Theme")
        theme_group = QActionGroup(self)
        theme_group.setExclusive(True)
        current_theme = self.settings.get("ui/theme") or theme.CURRENT_THEME
        for key, text in theme.THEME_LABELS.items():
            a = QAction(text, self, checkable=True, checked=(key == current_theme))
            a.triggered.connect(lambda _c, k=key: self._set_theme(k))
            theme_group.addAction(a)
            m_theme.addAction(a)

        m_density = m_view.addMenu("Density")
        density_group = QActionGroup(self)
        density_group.setExclusive(True)
        current_density = self.settings.get("ui/density") or theme.CURRENT_DENSITY
        for key, text in theme.DENSITY_LABELS.items():
            a = QAction(text, self, checkable=True, checked=(key == current_density))
            a.triggered.connect(lambda _c, k=key: self._set_density(k))
            density_group.addAction(a)
            m_density.addAction(a)

        m_help = mb.addMenu("&Help")
        act(m_help, "⌨ Keyboard shortcuts…", self._show_shortcuts, "?", "Help")
        m_help.addSeparator()
        act(m_help, "🔄 Check for Updates…", lambda: self._check_updates(silent=False))
        auto_update_act = QAction("Automatically check for updates", self, checkable=True,
                                  checked=bool(self.settings.get("updater/auto_check", True)))
        auto_update_act.triggered.connect(lambda on: self.settings.set("updater/auto_check", on))
        m_help.addAction(auto_update_act)
        m_help.addSeparator()
        act(m_help, "About PrismCut Studio", self._about)

    # ---------------------------------------------------------------- actions
    def _preview_media(self, media_id: str):
        item = self.project.media.get(media_id)
        if item:
            self.clip_monitor.show_media(item.path)

    def _open_in_studio(self, media_id: str):
        item = self.project.media.get(media_id)
        if item and item.kind == "image":
            self.photo.open_path(item.path)
            self.tabs.setCurrentWidget(self.photo)

    def _media_to_chat(self, media_id: str):
        item = self.project.media.get(media_id)
        if item:
            self.chat.attach_file(item.path)
            self.chat_dock.show()
            self.chat_dock.raise_()

    def _media_to_reference(self, media_id: str):
        item = self.project.media.get(media_id)
        if item:
            self.generate.add_reference(item.path)
            self.gen_dock.show()
            self.gen_dock.raise_()

    def _path_to_chat(self, path: str):
        self.chat.attach_file(path)
        self.chat_dock.show()
        self.chat_dock.raise_()

    def _path_to_reference(self, path: str):
        self.generate.add_reference(path)
        self.gen_dock.show()
        self.gen_dock.raise_()

    def _transcribe_media(self, media_id: str):
        item = self.project.media.get(media_id)
        if not item:
            return
        models = self.registry.models_with("transcribe")
        model = next((m for m in models
                      if self.settings.has_key(m.provider,
                                               (self.registry.provider(m.provider) or
                                                type("s", (), {"key_env": ""})).key_env)),
                     models[0] if models else None)
        if model is None:
            self.toast("No transcription model available.", "error")
            return
        adapter = self.get_adapter(model.provider)
        path = item.path

        def work(job):
            job.progress(-1, f"Transcribing {Path(path).name}")
            return adapter.transcribe(model.id, path)

        def done(text):
            from ..core import paths as p_
            out = p_.unique_path(p_.generated_dir(), Path(path).stem + "_transcript", ".txt")
            Path(out).write_text(str(text), encoding="utf-8")
            self.bin.add_generated(str(out), {"mode": "transcript"})
            self.toast("Transcript saved to bin ✓", "success")

        self.jobs.submit(f"Transcribe {Path(path).name}", work, on_done=done,
                         on_fail=lambda m: self.toast(f"Transcribe failed: {m}", "error", 8000))

    def _clip_selected(self, clip_id):
        self.effects.show_clip(clip_id)
        self.audio.show_clip(clip_id)
        if clip_id:
            self.fx_dock.show()

    def _show_effects_for(self, clip_id: str):
        self.effects.show_clip(clip_id)
        self.fx_dock.show()
        self.fx_dock.raise_()

    def _regenerate_pipeline_scene(self, pipeline_id: str, scene_id: str):
        """Timeline clip's "Regenerate this scene" action - the clip's
        underlying media already carries pipeline_id/scene_id (every
        pipeline-generated asset is tagged that way), so this works even for
        a movie that isn't the one currently open in the Movie Pipeline
        panel: load it there first, then regenerate."""
        if not (self.movie.run and self.movie.run.pipeline.id == pipeline_id):
            if not self.movie.load_pipeline_by_id(pipeline_id):
                return
        self.tabs.setCurrentWidget(self.movie)
        self.movie.run.regenerate_scene_current_stage(scene_id)

    def _razor_selected(self):
        clip = self.timeline.selected_clip()
        if clip:
            self.timeline.razor_at(clip.id, self.timeline.playhead_time)

    def add_track(self, kind: str):
        """Undoable track creation - the single primitive both the Timeline
        menu and Agent Mode's create_track tool call into."""
        track = self.project.add_track(kind)

        def insert(t=track):
            if t not in self.project.tracks:
                (self.project.tracks.insert(0, t) if t.kind == "video"
                 else self.project.tracks.append(t))

        def remove(t=track):
            if t in self.project.tracks:
                self.project.tracks.remove(t)

        def refresh():
            self.project.dirty = True
            self.timeline.refresh(True)
            self.audio.refresh_tracks()

        self.undo_stack.push(AddOrRemoveItemCommand(
            f"Add {track.name} track", add=True, insert=insert, remove=remove, refresh=refresh))
        return track

    def _generated(self, path: str, meta: dict):
        item = self.bin.add_generated(path, meta)
        kind = media_utils.kind_of(path)
        if kind == "image" and meta.get("mode") in ("image", None):
            self.clip_monitor.show_media(path)
        if kind == "video":
            self.clip_monitor.show_media(path)
        self.toast(f"Added to bin: {Path(path).name}", "success")
        return item

    def _lab_to_generate(self, prompt: str, kind: str):
        idx = {"image": 0, "video": 1, "music": 2}.get(kind, 0)
        self.generate.mode.setCurrentIndex(idx)
        self.generate.set_prompt(prompt)
        self.gen_dock.show()
        self.gen_dock.raise_()

    # ---------------------------------------------------------------- project
    def _rebind_project(self):
        self.undo_stack.clear()
        self.bin.set_project(self.project)
        self.timeline.set_project(self.project)
        self.project_monitor.set_project(self.project)
        self.effects.set_project(self.project)
        self.audio.set_project(self.project)
        self._sync_status()

    def new_project(self):
        self.project = Project()
        self._rebind_project()

    def open_project(self):
        f, _ = QFileDialog.getOpenFileName(self, "Open project", "",
                                           f"PrismCut project (*{PROJECT_EXT})")
        if f:
            self._open_path(f)

    def _open_path(self, path: str):
        try:
            self.project = Project.load(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Open failed", str(exc))
            self.settings.set("recent/projects",
                              [p for p in self.settings.recent("projects", 1000) if p != path])
            return
        self._rebind_project()
        self.settings.add_recent("projects", path)
        self._check_autosave_recovery(path)

    def _rebuild_recent_menu(self):
        self.m_recent.clear()
        paths = self.settings.recent("projects")
        if not paths:
            a = self.m_recent.addAction("(none yet)")
            a.setEnabled(False)
            return
        for p in paths:
            self.m_recent.addAction(p, lambda p=p: self._open_path(p))
        self.m_recent.addSeparator()
        self.m_recent.addAction("Clear recent projects",
                                lambda: self.settings.clear_recent("projects"))

    def save_project(self, save_as: bool = False):
        if save_as or not self.project.path:
            f, _ = QFileDialog.getSaveFileName(self, "Save project",
                                               f"untitled{PROJECT_EXT}",
                                               f"PrismCut project (*{PROJECT_EXT})")
            if not f:
                return
            self.project.path = Path(f)
        self.project.save()
        self.settings.add_recent("projects", str(self.project.path))
        self.toast(f"Saved {self.project.path}", "success")

    def export_dialog(self):
        ExportDialog(self.project, self.jobs, self.settings, self).exec()

    def keys_dialog(self):
        if KeysDialog(self.settings, self.registry, self.jobs, self).exec():
            self._adapters.clear()
            self.chat.model_combo.refresh()
            self.generate.model_combo.refresh()
            self.photo.nano.model_combo.refresh()
            self.audio.ai_model.refresh()
            self._sync_status()

    def model_manager(self):
        ModelManagerDialog(self.registry, self.settings, self).exec()
        for combo in (self.chat.model_combo, self.generate.model_combo,
                      self.photo.nano.model_combo, self.audio.ai_model):
            combo.refresh()

    def _toggle_fullscreen(self):
        """F11: true fullscreen that actually hides chrome - menu bar and
        every currently-visible dock, not just the window frame. Remembers
        exactly which docks were visible so exiting restores that precise
        state rather than blanket-showing everything."""
        docks = (self.bin_dock, self.fx_dock, self.history_dock, self.chat_dock,
                self.gen_dock, self.lab_dock, self.jobs_dock)
        if self.isFullScreen():
            self.showNormal()
            self.menuBar().setVisible(True)
            for dock in self._fullscreen_hidden_docks:
                dock.setVisible(True)
            self._fullscreen_hidden_docks = []
        else:
            self._fullscreen_hidden_docks = [d for d in docks if d.isVisible()]
            for dock in self._fullscreen_hidden_docks:
                dock.setVisible(False)
            self.menuBar().setVisible(False)
            self.showFullScreen()

    def _show_shortcuts(self):
        if getattr(self, "_shortcuts_dialog", None) is None:
            self._shortcuts_dialog = ShortcutsDialog(self)
        self._shortcuts_dialog.show()
        self._shortcuts_dialog.raise_()
        self._shortcuts_dialog.activateWindow()

    def _set_theme(self, name: str):
        self.settings.set("ui/theme", name)
        theme.set_theme(QApplication.instance(), name,
                        self.settings.get("ui/density", theme.DEFAULT_DENSITY))
        self._refresh_custom_paint()

    def _set_density(self, name: str):
        self.settings.set("ui/density", name)
        theme.set_theme(QApplication.instance(), self.settings.get("ui/theme", theme.DEFAULT_THEME),
                        name)
        self._refresh_custom_paint()

    def _refresh_custom_paint(self):
        """QSS repaints most widgets automatically, but content that's
        custom-drawn (QGraphicsItem.paint, or a pre-composited pixmap) and
        only reads theme.* at the moment it draws needs an explicit nudge
        to actually redraw with the new palette."""
        self.timeline.refresh(True)
        self.photo.canvas.viewport().update()
        self.photo.refresh_theme()

    def _maybe_check_updates_on_startup(self):
        if not bool(self.settings.get("updater/auto_check", True)):
            return
        last = float(self.settings.get("updater/last_check_ts", 0) or 0)
        if time.time() - last < updater.CHECK_INTERVAL_SECS:
            return
        # Written BEFORE the check actually runs, so a check still in flight
        # at the next launch/close doesn't cause a re-check storm.
        self.settings.set("updater/last_check_ts", time.time())
        self._check_updates(silent=True)

    def _check_updates(self, silent: bool):
        def work(_job):
            return updater.check_latest()

        def done(release):
            if release is None:
                if not silent:
                    self.toast("Could not check for updates — try again later.", "error")
                return
            if not updater.is_newer(release.tag):
                if not silent:
                    self.toast(f"You're up to date ({__version__}).", "success")
                return
            if silent and str(self.settings.get("updater/skip_version", "")) == release.tag:
                return
            UpdateDialog(release, self.jobs, self.settings, self).exec()

        def fail(msg):
            if not silent:
                self.toast(f"Update check failed: {msg}", "error")

        self.jobs.submit("Check for updates", work, kind="update_check",
                         on_done=done, on_fail=fail)

    def _about(self):
        QMessageBox.about(
            self, "About",
            f"<b>{APP_NAME}</b> v{__version__}<br><br>"
            "Open-source AI photo, video &amp; audio studio - bring your own keys.<br>"
            "Layout inspired by the excellent <a href='https://kdenlive.org'>Kdenlive</a> "
            "(clean-room, no code shared).<br><br>"
            "MIT license · <a href='https://github.com'>Source on GitHub</a>")

    def _sync_status(self):
        keys = sum(1 for name, spec in self.registry.providers.items()
                   if self.settings.has_key(name, spec.key_env))
        ff = "ffmpeg ✓" if media_utils.have_ffmpeg() else "⚠ ffmpeg missing (export disabled)"
        active = self.jobs.active_count()
        jobs_txt = f" · {active} job(s) running" if active else ""
        self.statusBar().showMessage("")
        self.statusBar().clearMessage()
        perm = getattr(self, "_perm_label", None)
        if perm is None:
            from PySide6.QtWidgets import QLabel
            perm = QLabel()
            self._perm_label = perm
            self.statusBar().addPermanentWidget(perm)
        perm.setText(f"🔑 {keys}/{len(self.registry.providers)} providers · {ff}"
                     f" · {self.project.duration():.1f}s timeline{jobs_txt}")
        if getattr(self, "_saving_now", False):
            pass  # _autosave_tick owns the "Saving…" state while it's active
        else:
            self._set_save_indicator("unsaved" if self.project.dirty else "saved")

    def _set_save_indicator(self, state: str):
        text, color = {
            "saved": ("✓ All changes saved", theme.TEXT_DIM),
            "saving": ("⟳ Saving…", theme.TEXT_DIM),
            "unsaved": ("● Unsaved changes", theme.ORANGE),
        }[state]
        self._save_label.setText(text)
        self._save_label.setStyleSheet(f"color:{color};")

    def _autosave_tick(self):
        if not self.project.dirty or not self.project.clips:
            return
        self._saving_now = True
        self._set_save_indicator("saving")
        try:
            target = paths.autosave_path_for(self.project.path)
            target.write_text(json.dumps(self.project.to_dict(), indent=2), encoding="utf-8")
        except OSError:
            pass  # autosave is best-effort - never interrupt the user for it
        self._saving_now = False
        self._set_save_indicator("unsaved")   # the REAL file is still unsaved - only the autosave landed

    def _check_autosave_recovery(self, project_path):
        auto = paths.autosave_path_for(project_path)
        if not auto.exists():
            return
        real_mtime = Path(project_path).stat().st_mtime if project_path and Path(project_path).exists() else 0
        if auto.stat().st_mtime <= real_mtime:
            return
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(auto.stat().st_mtime))
        resp = QMessageBox.question(
            self, "Recover unsaved work?",
            f"Found autosaved changes from {when} that are newer than your last save. "
            "Load them instead?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if resp == QMessageBox.StandardButton.Yes:
            recovered = Project.load(auto)
            recovered.path = Path(project_path) if project_path else None
            recovered.dirty = True
            self.project = recovered
            self._rebind_project()
            self.toast("Recovered autosaved changes — remember to save.", "info")
        # Either way, this autosave has now been offered and acted on - delete
        # it so a "No" (or an untitled project with no real file to compare
        # mtimes against) doesn't re-prompt on every future launch forever.
        try:
            auto.unlink()
        except OSError:
            pass

    def _restore_layout(self):
        state = self.settings.q.value("layout/windowState")
        geometry = self.settings.q.value("layout/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        if state is not None:
            self.restoreState(state)

    def _save_layout(self):
        self.settings.q.setValue("layout/windowState", self.saveState())
        self.settings.q.setValue("layout/geometry", self.saveGeometry())

    def closeEvent(self, ev):
        if self.project.dirty and self.project.clips:
            resp = QMessageBox.question(
                self, "Unsaved changes", "Save the project before closing?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel)
            if resp == QMessageBox.StandardButton.Save:
                self.save_project()
            elif resp == QMessageBox.StandardButton.Cancel:
                ev.ignore()
                return
        if not self.isFullScreen():   # don't persist the fullscreen-hidden layout as the normal one
            self._save_layout()
        ev.accept()
