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

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QMainWindow,
                               QMessageBox, QSplitter, QTabWidget, QVBoxLayout,
                               QWidget)

from .. import APP_NAME, __version__
from ..core import media as media_utils
from ..core.jobs import JobManager
from ..core.project import PROJECT_EXT, Project
from ..core.registry import Registry
from ..core.settings import Settings
from ..providers import make_adapter
from .dialogs.export_dialog import ExportDialog
from .dialogs.keys_dialog import KeysDialog
from .dialogs.model_manager import ModelManagerDialog
from .panels.audio_panel import AudioPanel
from .panels.chat_panel import ChatPanel
from .panels.effects_panel import EffectsPanel
from .panels.generate_panel import GeneratePanel
from .panels.jobs_panel import JobsPanel
from .panels.monitor import ClipMonitor, ProjectMonitor
from .panels.photo_studio import PhotoStudio
from .panels.project_bin import ProjectBin
from .panels.prompt_lab import PromptLab
from .panels.timeline import TimelineWidget


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

        # ------------------------------------------------------------ panels
        self.bin = ProjectBin(self.project)
        self.clip_monitor = ClipMonitor()
        self.project_monitor = ProjectMonitor(self.project)
        self.timeline = TimelineWidget(self.project)
        self.effects = EffectsPanel(self.project)
        self.chat = ChatPanel(self.registry, self.settings, self.get_adapter)
        self.generate = GeneratePanel(self.registry, self.settings, self.jobs,
                                      self.get_adapter)
        self.prompt_lab = PromptLab()
        self.photo = PhotoStudio(self.registry, self.settings, self.jobs, self.get_adapter)
        self.audio = AudioPanel(self.project, self.registry, self.settings, self.jobs,
                                self.get_adapter)
        self.jobs_panel = JobsPanel(self.jobs)

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
        self.setCentralWidget(self.tabs)

        # ------------------------------------------------------------ docks
        self.bin_dock = self._dock("Project Bin", self.bin,
                                   Qt.DockWidgetArea.LeftDockWidgetArea)
        self.fx_dock = self._dock("Effects", self.effects,
                                  Qt.DockWidgetArea.LeftDockWidgetArea)
        self.tabifyDockWidget(self.bin_dock, self.fx_dock)
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

        self._wire()
        self._build_menus()
        self._sync_status()

    # ---------------------------------------------------------------- helpers
    def _dock(self, title: str, widget: QWidget, area) -> QDockWidget:
        d = QDockWidget(title, self)
        d.setWidget(widget)
        d.setObjectName(title.replace(" ", "_"))
        self.addDockWidget(area, d)
        return d

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
        # timeline
        self.timeline.playheadMoved.connect(self.project_monitor.preview_at)
        self.timeline.selectionChanged.connect(self._clip_selected)
        self.timeline.effectsRequested.connect(self._show_effects_for)
        self.timeline.timelineChanged.connect(self._sync_status)
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
        # status line
        for src in (self.photo.status, self.generate.status, self.audio.status,
                    self.chat.status, self.prompt_lab.status):
            src.connect(lambda msg: self.statusBar().showMessage(msg, 6000))
        self.jobs.jobsChanged.connect(self._sync_status)

    # ---------------------------------------------------------------- menus
    def _build_menus(self):
        mb = self.menuBar()

        def act(menu, text, fn, shortcut=None):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.triggered.connect(fn)
            menu.addAction(a)
            return a

        m_file = mb.addMenu("&File")
        act(m_file, "🆕 New project", self.new_project, "Ctrl+N")
        act(m_file, "📂 Open project…", self.open_project, "Ctrl+O")
        act(m_file, "💾 Save project", self.save_project, "Ctrl+S")
        act(m_file, "Save project as…", lambda: self.save_project(True))
        m_file.addSeparator()
        act(m_file, "📥 Import media…", self.bin.import_dialog, "Ctrl+I")
        act(m_file, "🎬 Export / render…", self.export_dialog, "Ctrl+E")
        m_file.addSeparator()
        act(m_file, "Quit", self.close, "Ctrl+Q")

        m_tl = mb.addMenu("&Timeline")
        act(m_tl, "✂ Razor selected at playhead", self._razor_selected, "R")
        act(m_tl, "🗑 Delete selected clip", self.timeline.delete_selected, "Del")
        act(m_tl, "＋ Add video track", lambda: (self.project.add_track("video"),
                                                self.timeline.refresh(True),
                                                self.audio.refresh_tracks()))
        act(m_tl, "＋ Add audio track", lambda: (self.project.add_track("audio"),
                                                self.timeline.refresh(True),
                                                self.audio.refresh_tracks()))

        m_ai = mb.addMenu("&AI")
        act(m_ai, "🔑 API Keys…", self.keys_dialog, "Ctrl+K")
        act(m_ai, "🗂 Model Manager…", self.model_manager, "Ctrl+M")
        m_ai.addSeparator()
        act(m_ai, "💬 Chat panel", lambda: (self.chat_dock.show(), self.chat_dock.raise_()))
        act(m_ai, "🚀 Generate panel", lambda: (self.gen_dock.show(), self.gen_dock.raise_()))
        act(m_ai, "🧪 Prompt Lab", lambda: (self.lab_dock.show(), self.lab_dock.raise_()))

        m_view = mb.addMenu("&View")
        for dock in (self.bin_dock, self.fx_dock, self.chat_dock, self.gen_dock,
                     self.lab_dock, self.jobs_dock):
            m_view.addAction(dock.toggleViewAction())

        m_help = mb.addMenu("&Help")
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
            self.statusBar().showMessage("No transcription model available.", 6000)
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
            self.statusBar().showMessage("Transcript saved to bin ✓", 6000)

        self.jobs.submit(f"Transcribe {Path(path).name}", work, on_done=done,
                         on_fail=lambda m: self.statusBar().showMessage(
                             f"Transcribe failed: {m}", 8000))

    def _clip_selected(self, clip_id):
        self.effects.show_clip(clip_id)
        self.audio.show_clip(clip_id)
        if clip_id:
            self.fx_dock.show()

    def _show_effects_for(self, clip_id: str):
        self.effects.show_clip(clip_id)
        self.fx_dock.show()
        self.fx_dock.raise_()

    def _razor_selected(self):
        clip = self.timeline.selected_clip()
        if clip:
            self.timeline.razor_at(clip.id, self.timeline.playhead_time)

    def _generated(self, path: str, meta: dict):
        item = self.bin.add_generated(path, meta)
        kind = media_utils.kind_of(path)
        if kind == "image" and meta.get("mode") in ("image", None):
            self.clip_monitor.show_media(path)
        if kind == "video":
            self.clip_monitor.show_media(path)
        self.statusBar().showMessage(f"Added to bin: {Path(path).name}", 6000)
        return item

    def _lab_to_generate(self, prompt: str, kind: str):
        idx = {"image": 0, "video": 1, "music": 2}.get(kind, 0)
        self.generate.mode.setCurrentIndex(idx)
        self.generate.set_prompt(prompt)
        self.gen_dock.show()
        self.gen_dock.raise_()

    # ---------------------------------------------------------------- project
    def _rebind_project(self):
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
        if not f:
            return
        try:
            self.project = Project.load(f)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Open failed", str(exc))
            return
        self._rebind_project()

    def save_project(self, save_as: bool = False):
        if save_as or not self.project.path:
            f, _ = QFileDialog.getSaveFileName(self, "Save project",
                                               f"untitled{PROJECT_EXT}",
                                               f"PrismCut project (*{PROJECT_EXT})")
            if not f:
                return
            self.project.path = Path(f)
        self.project.save()
        self.statusBar().showMessage(f"Saved {self.project.path}", 5000)

    def export_dialog(self):
        ExportDialog(self.project, self.jobs, self).exec()

    def keys_dialog(self):
        if KeysDialog(self.settings, self.registry, self.jobs, self).exec():
            self._adapters.clear()
            self.chat.model_combo.refresh()
            self.generate.model_combo.refresh()
            self.photo.nano.model_combo.refresh()
            self.audio.ai_model.refresh()
            self._sync_status()

    def model_manager(self):
        ModelManagerDialog(self.registry, self).exec()
        for combo in (self.chat.model_combo, self.generate.model_combo,
                      self.photo.nano.model_combo, self.audio.ai_model):
            combo.refresh()

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
        ev.accept()
