"""Movie Pipeline panel: describe-a-movie -> scripted, storyboarded, voiced,
generated scenes, laid out on the timeline automatically. The deterministic
stage sequence itself lives in core.pipeline_orchestrator.PipelineRun; this
panel is just the two confirm-gated stage buttons plus one row per scene -
the Jobs dock stays the single progress source of truth (per-scene rows show
only a status icon, click it to raise Jobs), matching how main_window.py
already raises the Effects dock on clip selection rather than duplicating
progress UI locally."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QInputDialog, QLabel, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from ...core import media as media_utils
from ...core.pipeline import MoviePipeline, Scene
from ...core.pipeline_orchestrator import PipelineRun
from ..dialogs.new_pipeline_dialog import NewPipelineDialog
from ..widgets.common import STATUS_ICONS, DropAcceptor, accent_button, confirm_destructive, label

BUSY_STATUSES = ("images_running", "video_running")


def _scene_status_icon(scene: Scene) -> str:
    if scene.video.active:
        return STATUS_ICONS["done"]
    if scene.image.active:
        return "🖼"
    return STATUS_ICONS["queued"]


class SceneRow(QWidget):
    jobsRequested = Signal()

    def __init__(self, run: PipelineRun, scene: Scene, parent=None):
        super().__init__(parent)
        self.run = run
        self.scene = scene

        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 3, 4, 3)

        self.thumb = QLabel()
        self.thumb.setFixedSize(64, 40)
        self.thumb.setScaledContents(True)
        self.thumb.setStyleSheet("background:rgba(127,127,127,40);border-radius:4px;")

        self.icon = QLabel()
        self.icon.setFixedWidth(20)
        self.icon.setToolTip("Click to see live progress in the Jobs panel")
        self.icon.mousePressEvent = lambda _ev: self.jobsRequested.emit()

        self.title = QLabel()
        self.title.setWordWrap(True)

        self.edit_btn = QPushButton("✎")
        self.edit_btn.setFixedSize(26, 24)
        self.edit_btn.setToolTip("Edit this scene's script / visual prompt")
        self.edit_btn.clicked.connect(self._edit_prompt)

        self.regen_btn = QPushButton("🔄")
        self.regen_btn.setFixedSize(26, 24)
        self.regen_btn.setToolTip("Regenerate this scene's image (uses the prompt above)")
        self.regen_btn.clicked.connect(self._regenerate)

        lay.addWidget(self.thumb)
        lay.addWidget(self.icon)
        lay.addWidget(self.title, 1)
        lay.addWidget(self.edit_btn)
        lay.addWidget(self.regen_btn)

        self.setToolTip("Drop an image here to use it as this scene's picture instead of "
                        "generating one - AI generation for this scene is then skipped.")
        DropAcceptor(self, ("image",), self._on_drop)

        run.sceneChanged.connect(self._on_scene_changed)
        self.sync()

    def _on_scene_changed(self, scene_id: str):
        if scene_id == self.scene.id:
            self.sync()

    def sync(self):
        self.icon.setText(_scene_status_icon(self.scene))
        n = self.scene.index + 1
        script = self.scene.script.strip() or "(no script yet)"
        self.title.setText(f"<b>Scene {n}</b> — {script[:110]}")
        self.title.setToolTip(self.scene.script)
        img = self.scene.image.active
        media = self.run.win.project.media.get(img.media_id) if img else None
        thumb_path = media_utils.thumbnail(media.path) if media else None
        self.thumb.setPixmap(QPixmap(str(thumb_path)) if thumb_path else QPixmap())

    def _edit_prompt(self):
        text, ok = QInputDialog.getMultiLineText(
            self, f"Scene {self.scene.index + 1} prompt", "Script / visual prompt:",
            self.scene.script)
        if ok:
            self.scene.script = text
            self.run.pipeline.save()
            self.sync()

    def _regenerate(self):
        self.run.regenerate_scene_image(self.scene.id)

    def _on_drop(self, paths_: list[str]):
        if paths_:
            self.run.set_scene_image_override(self.scene.id, paths_[0])


class MoviePipelinePanel(QWidget):
    status = Signal(str)
    jobsRequested = Signal()

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.win = main_window
        self.registry = main_window.registry
        self.settings = main_window.settings
        self.run: PipelineRun | None = None
        self._rows: dict[str, SceneRow] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        top = QHBoxLayout()
        new_btn = accent_button("🎬 New movie…")
        new_btn.clicked.connect(self._new_pipeline)
        self.load_combo = QComboBox()
        self.load_combo.setMinimumWidth(220)
        self.load_combo.activated.connect(self._load_selected)
        top.addWidget(new_btn)
        top.addWidget(self.load_combo, 1)
        outer.addLayout(top)

        self.summary = label("No movie loaded yet — click “New movie…” to describe one.",
                             dim=True)
        outer.addWidget(self.summary)

        stage_row = QHBoxLayout()
        self.images_btn = accent_button("🖼 Generate all scene images")
        self.images_btn.setToolTip("Stage 1 of 2 — queues one image-generation call per "
                                   "scene that doesn't already have an image.")
        self.images_btn.clicked.connect(self._run_images)
        self.video_btn = accent_button("🎥 Generate all scene video")
        self.video_btn.setToolTip("Stage 2 of 2 (most expensive) — queues one video "
                                  "generation per scene, plus a lip-sync pass per scene "
                                  "if a lip-sync model is configured.")
        self.video_btn.clicked.connect(self._run_video)
        stage_row.addWidget(self.images_btn)
        stage_row.addWidget(self.video_btn)
        outer.addLayout(stage_row)

        self.list_host = QWidget()
        self.list_lay = QVBoxLayout(self.list_host)
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self.list_lay.setSpacing(1)
        self.list_lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(self.list_host)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll, 1)

        self._refresh_load_combo()
        self._sync_buttons()

    # ------------------------------------------------------------- lifecycle
    def _refresh_load_combo(self):
        self.load_combo.blockSignals(True)
        self.load_combo.clear()
        self.load_combo.addItem("(load a saved movie…)", "")
        for p in MoviePipeline.list_saved():
            self.load_combo.addItem(p.stem, str(p))
        self.load_combo.blockSignals(False)

    def _load_selected(self, idx: int):
        path = self.load_combo.itemData(idx)
        if path:
            self._set_pipeline(MoviePipeline.load(path))

    def _new_pipeline(self):
        dlg = NewPipelineDialog(self.registry, self.settings, self.win)
        if dlg.exec() and dlg.pipeline:
            dlg.pipeline.save()
            self._refresh_load_combo()
            self._set_pipeline(dlg.pipeline)
            self.status.emit(f"Writing scene breakdown for “{dlg.pipeline.name}”…")
            self.run.generate_breakdown(
                on_fail=lambda msg: self.status.emit(f"Script breakdown failed: {msg}"))

    def _set_pipeline(self, pipeline: MoviePipeline):
        if self.run:
            self.run.logMessage.disconnect(self._on_log)
            self.run.statusChanged.disconnect(self._on_status)
            self.run.sceneChanged.disconnect(self._on_scene_list_maybe_changed)
        self.run = PipelineRun(self.win, pipeline)
        self.run.logMessage.connect(self._on_log)
        self.run.statusChanged.connect(self._on_status)
        self.run.sceneChanged.connect(self._on_scene_list_maybe_changed)
        self._rebuild_scene_rows()
        self._sync_buttons()

    def _on_log(self, msg: str):
        self.status.emit(msg)

    def _on_status(self, _status: str):
        self._sync_buttons()

    def _on_scene_list_maybe_changed(self, _scene_id: str):
        # A scene's first asset landing can be the moment scenes go from "not
        # rendered as rows yet" (right after generate_breakdown) to needing
        # rows - cheapest correct check is just comparing row count to scene
        # count rather than tracking that transition explicitly.
        if self.run and len(self._rows) != len(self.run.pipeline.scenes):
            self._rebuild_scene_rows()
        self._sync_buttons()

    def _rebuild_scene_rows(self):
        for row in self._rows.values():
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()
        if not self.run:
            return
        for scene in sorted(self.run.pipeline.scenes, key=lambda s: s.index):
            row = SceneRow(self.run, scene)
            row.jobsRequested.connect(self.jobsRequested)
            self._rows[scene.id] = row
            self.list_lay.insertWidget(self.list_lay.count() - 1, row)

    def _sync_buttons(self):
        has_run = self.run is not None
        p = self.run.pipeline if has_run else None
        if p:
            self.summary.setText(f"“{p.name}” · {len(p.scenes)} scene(s) · status: {p.status}")
        else:
            self.summary.setText("No movie loaded yet — click “New movie…” to describe one.")
        busy = bool(p and p.status in BUSY_STATUSES)
        self.images_btn.setEnabled(bool(p and p.scenes) and not busy)
        self.video_btn.setEnabled(bool(p and p.scenes and any(s.image.active for s in p.scenes))
                                  and not busy)

    # ---------------------------------------------------------------- gates
    def _run_images(self):
        if not self.run:
            return
        n = sum(1 for s in self.run.pipeline.scenes if s.image.active is None)
        if n == 0:
            self.status.emit("Every scene already has an image.")
            return
        if not confirm_destructive(
                self.win, self.settings, "pipeline_run_image_batch",
                "Generate scene images",
                f"This queues {n} image-generation call(s), one per scene, using your "
                "configured image model. Each call spends your own API credits. Continue?",
                "Generate"):
            return
        self.run.run_image_batch()

    def _run_video(self):
        if not self.run:
            return
        n = sum(1 for s in self.run.pipeline.scenes if s.video.active is None)
        if n == 0:
            self.status.emit("Every scene already has a video.")
            return
        lipsync_note = (" plus a lip-sync pass" if self.run.pipeline.lipsync_model else "")
        if not confirm_destructive(
                self.win, self.settings, "pipeline_run_video_batch",
                "Generate scene video",
                f"This queues {n} video-generation call(s){lipsync_note}, one per scene — "
                "the most expensive stage. Each call spends your own API credits. Continue?",
                "Generate"):
            return
        self.run.run_video_batch()
