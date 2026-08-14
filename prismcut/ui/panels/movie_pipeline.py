"""Movie Pipeline panel: describe-a-movie -> scripted, storyboarded, voiced,
generated scenes, laid out on the timeline automatically. The deterministic
stage sequence itself lives in core.pipeline_orchestrator.PipelineRun; this
panel is just the two confirm-gated stage buttons plus one row per scene -
the Jobs dock stays the single progress source of truth (per-scene rows show
only a status icon, click it to raise Jobs), matching how main_window.py
already raises the Effects dock on clip selection rather than duplicating
progress UI locally."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QInputDialog, QLabel, QPlainTextEdit,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ...core import cost_estimator
from ...core import media as media_utils
from ...core import paths
from ...core.pipeline import MoviePipeline, Scene
from ...core.pipeline_orchestrator import PipelineRun
from ..dialogs.new_pipeline_dialog import NewPipelineDialog
from ..widgets.common import (STATUS_ICONS, CollapsibleSection, DropAcceptor, ModelCombo,
                              accent_button, confirm_destructive, label)
from .generate_panel import ParamForm

BUSY_STATUSES = ("images_running", "video_running")


def _scene_status_icon(scene: Scene) -> str:
    # Checked first regardless of what's already been generated: a scene
    # whose most recent attempt (any stage) failed needs attention even if
    # an earlier stage succeeded, e.g. a good image but a rejected video.
    if scene.last_error:
        return STATUS_ICONS["error"]
    if scene.video.active:
        return STATUS_ICONS["done"]
    if scene.image.active:
        return "🖼"
    return STATUS_ICONS["queued"]


class SceneRow(QWidget):
    jobsRequested = Signal()
    jumpRequested = Signal(str)   # scene_id

    def __init__(self, run: PipelineRun, scene: Scene, parent=None):
        super().__init__(parent)
        self.run = run
        self.scene = scene

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 3, 4, 3)
        outer.setSpacing(2)

        lay = QHBoxLayout()

        self.thumb = QLabel()
        self.thumb.setFixedSize(64, 40)
        self.thumb.setScaledContents(True)
        self.thumb.setStyleSheet("background:rgba(127,127,127,40);border-radius:4px;")
        self.thumb.setToolTip("Click to jump to this scene on the timeline")
        self.thumb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.thumb.mousePressEvent = lambda _ev: self.jumpRequested.emit(self.scene.id)

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
        self.regen_btn.clicked.connect(self._regenerate)

        lay.addWidget(self.thumb)
        lay.addWidget(self.icon)
        lay.addWidget(self.title, 1)
        lay.addWidget(self.edit_btn)
        lay.addWidget(self.regen_btn)
        outer.addLayout(lay)

        # Persistent (not tooltip-only) failure notice - set/cleared by
        # sync() from scene.last_error, which the orchestrator writes on any
        # stage's generation failure (API error, moderation rejection, ...).
        # Visible without expanding Details, since knowing a scene needs a
        # retry is the whole point.
        self.error_label = label("", dim=False)
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        outer.addWidget(self.error_label)

        self.details = self._build_details()
        self.section = CollapsibleSection("Details — script & generation params", self.details,
                                          expanded=False)
        outer.addWidget(self.section)

        self.setToolTip("Drop an image here to use it as this scene's picture instead of "
                        "generating one - AI generation for this scene is then skipped.")
        DropAcceptor(self, ("image",), self._on_drop)

        run.sceneChanged.connect(self._on_scene_changed)
        self.sync()

    def _build_details(self) -> QWidget:
        """Reviewable/editable script + per-scene model overrides
        (Scene.image_model/video_model, "" = inherit the pipeline's own
        choice) + per-scene image/video param overrides (Scene.image_params/
        video_params, layered onto whichever model ends up in effect at
        generation time - see pipeline_orchestrator._generate_scene_image/
        _video). The param forms rebuild live when a row's model override
        changes, since a different model can have a different params
        schema than the pipeline default."""
        host = QWidget()
        v = QVBoxLayout(host)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(4)

        v.addWidget(label("Script / visual prompt", dim=True))
        self.script_edit = QPlainTextEdit(self.scene.script)
        self.script_edit.setMaximumHeight(90)
        self.script_edit.setPlaceholderText("Describe what happens in this scene…")
        v.addWidget(self.script_edit)

        registry = self.run.win.registry
        settings = self.run.win.settings

        v.addWidget(label("Image model (optional override)", dim=True))
        self.image_model_combo = ModelCombo(registry, settings, ("image_generate",),
                                            allow_none=True,
                                            none_label="— Use pipeline default —")
        self._select_override(self.image_model_combo, self.scene.image_model)
        self.image_model_combo.currentIndexChanged.connect(self._rebuild_image_param_form)
        v.addWidget(self.image_model_combo)
        self.image_param_label = label("Image generation parameters", dim=True)
        v.addWidget(self.image_param_label)
        self.image_param_form = ParamForm()
        v.addWidget(self.image_param_form)

        v.addWidget(label("Video model (optional override)", dim=True))
        self.video_model_combo = ModelCombo(registry, settings, ("video_generate",),
                                            allow_none=True,
                                            none_label="— Use pipeline default —")
        self._select_override(self.video_model_combo, self.scene.video_model)
        self.video_model_combo.currentIndexChanged.connect(self._rebuild_video_param_form)
        v.addWidget(self.video_model_combo)
        self.video_param_label = label("Video generation parameters (length, quality, ...)",
                                       dim=True)
        v.addWidget(self.video_param_label)
        self.video_param_form = ParamForm()
        v.addWidget(self.video_param_form)

        self._rebuild_image_param_form()
        self._rebuild_video_param_form()

        save_btn = QPushButton("💾 Save changes")
        save_btn.setToolTip("Saves the script, model overrides, and any parameter overrides "
                            "above - applied next time this scene is (re)generated.")
        save_btn.clicked.connect(self._save_details)
        v.addWidget(save_btn)
        return host

    @staticmethod
    def _select_override(combo: ModelCombo, key: str) -> None:
        idx = combo.findData(key) if key else 0
        combo.setCurrentIndex(idx if idx >= 0 else 0)

    def _effective_image_model(self):
        return (self.image_model_combo.current_model()
               or self.run.win.registry.by_key(self.run.pipeline.image_model))

    def _effective_video_model(self):
        return (self.video_model_combo.current_model()
               or self.run.win.registry.by_key(self.run.pipeline.video_model))

    def _rebuild_image_param_form(self, _idx: int = 0):
        model = self._effective_image_model()
        has_params = bool(model and model.params)
        self.image_param_label.setVisible(has_params)
        self.image_param_form.setVisible(has_params)
        if has_params:
            self.image_param_form.build(model, self.scene.image_params)

    def _rebuild_video_param_form(self, _idx: int = 0):
        model = self._effective_video_model()
        has_params = bool(model and model.params)
        self.video_param_label.setVisible(has_params)
        self.video_param_form.setVisible(has_params)
        if has_params:
            self.video_param_form.build(model, self.scene.video_params)

    def _save_details(self):
        self.scene.script = self.script_edit.toPlainText()
        self.scene.image_model = self.image_model_combo.current_key()
        self.scene.video_model = self.video_model_combo.current_key()
        self.scene.image_params = self.image_param_form.values()
        self.scene.video_params = self.video_param_form.values()
        self.run.pipeline.save()
        self.sync()
        self.run.win.toast(f"Scene {self.scene.index + 1} changes saved.", "success")

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
        if self.scene.last_error:
            from .. import theme
            self.error_label.setText(f"⚠ {self.scene.last_error}")
            self.error_label.setStyleSheet(f"color:{theme.DANGER};")
            self.error_label.setVisible(True)
            self.regen_btn.setToolTip("Retry this scene - if it was rejected by moderation or "
                                      "an API constraint, edit the script/prompt in Details "
                                      "below first, then click to try again.")
        else:
            self.error_label.setVisible(False)
            self.regen_btn.setToolTip("Regenerate this scene (uses the prompt above) - redoes "
                                      "the video if one exists yet, otherwise the image")
        # Don't clobber an in-progress inline edit: a job finishing for this
        # scene fires sceneChanged -> sync() while the user may be mid-edit
        # in script_edit (e.g. regenerating video while tweaking the next
        # scene's script). A focused field means the user owns its text.
        if not self.script_edit.hasFocus() and self.script_edit.toPlainText() != self.scene.script:
            self.script_edit.setPlainText(self.scene.script)

    def _edit_prompt(self):
        text, ok = QInputDialog.getMultiLineText(
            self, f"Scene {self.scene.index + 1} prompt", "Script / visual prompt:",
            self.scene.script)
        if ok:
            self.scene.script = text
            self.run.pipeline.save()
            self.sync()

    def _regenerate(self):
        self.run.regenerate_scene_current_stage(self.scene.id)

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
        new_btn.clicked.connect(self.new_pipeline)
        self.load_combo = QComboBox()
        self.load_combo.setMinimumWidth(220)
        self.load_combo.activated.connect(self._load_selected)
        top.addWidget(new_btn)
        top.addWidget(self.load_combo, 1)
        outer.addLayout(top)

        self.summary = label("No movie loaded yet — click “New movie…” to describe one.",
                             dim=True)
        outer.addWidget(self.summary)

        # Persistent (not just a 5s toast) record of the last script-breakdown
        # attempt - a failure here (bad JSON, provider declined the brief,
        # network error) used to be visible only as a transient toast, easy
        # to miss, after which the panel just sat there with 0 scenes and no
        # visible explanation.
        self.script_status = label("", dim=True)
        self.script_status.setWordWrap(True)
        outer.addWidget(self.script_status)

        self.retry_script_btn = QPushButton("🔄 Retry script breakdown")
        self.retry_script_btn.setToolTip(
            "Re-sends the same brief to the script model. Useful if the last attempt failed "
            "or the AI declined to break it down.")
        self.retry_script_btn.clicked.connect(self._retry_script)
        self.retry_script_btn.setVisible(False)
        outer.addWidget(self.retry_script_btn)
        self._script_running = False

        size_row = QHBoxLayout()
        size_row.addWidget(label("Batch size:", dim=True))
        self.batch_size_combo = QComboBox()
        self.batch_size_combo.addItem("1 scene", 1)
        self.batch_size_combo.addItem("5 scenes", 5)
        self.batch_size_combo.addItem("10 scenes", 10)
        self.batch_size_combo.addItem("All remaining", None)
        self.batch_size_combo.setCurrentIndex(3)
        self.batch_size_combo.setToolTip(
            "How many scenes each click of Generate/Continue below queues - pick a small "
            "number to preview style/quality on a few scenes before committing to the full "
            "(and most expensive) batch. Generate/Continue always picks up with whichever "
            "scenes still need that stage, in order, so this doubles as resuming a "
            "previous run.")
        size_row.addWidget(self.batch_size_combo)
        size_row.addStretch(1)
        outer.addLayout(size_row)

        stage_row = QHBoxLayout()
        self.images_btn = accent_button("🖼 Generate scene images")
        self.images_btn.clicked.connect(self._run_images)
        self.video_btn = accent_button("🎥 Generate scene video")
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

    def load_pipeline_by_id(self, pipeline_id: str) -> bool:
        """Loads and shows a saved pipeline by id - e.g. for a timeline
        clip's "Regenerate this scene" action reaching a movie that isn't
        the one currently open in this panel. Returns whether it succeeded."""
        path = paths.pipelines_dir() / f"{pipeline_id}.json"
        if not path.exists():
            self.status.emit("Couldn't find that movie's saved pipeline file.")
            return False
        try:
            pipeline = MoviePipeline.load(path)
        except Exception as exc:  # noqa: BLE001
            self.status.emit(f"Couldn't load that movie: {exc}")
            return False
        self._set_pipeline(pipeline)
        self._refresh_load_combo()
        return True

    def _load_selected(self, idx: int):
        path = self.load_combo.itemData(idx)
        if path:
            self._set_pipeline(MoviePipeline.load(path))

    def new_pipeline(self):
        dlg = NewPipelineDialog(self.registry, self.settings, self.win.jobs, self.win.get_adapter,
                                self.win)
        if dlg.exec() and dlg.pipeline:
            dlg.pipeline.save()
            self._refresh_load_combo()
            self._set_pipeline(dlg.pipeline)
            if dlg.pipeline.scenes:
                # Scenes already came from "Import my own script..." in the
                # dialog - running the normal invent-from-brief breakdown
                # here would silently overwrite them. Audio still needs
                # kicking off explicitly, same as generate_breakdown's own
                # done() callback does for the invented-scenes path.
                self.run.run_audio_batch()
            else:
                self._run_script_breakdown()

    def _retry_script(self):
        self._run_script_breakdown()

    def _run_script_breakdown(self):
        if not self.run or self._script_running:
            return
        self._script_running = True
        self.retry_script_btn.setEnabled(False)
        self._set_script_status(f"Writing scene breakdown for “{self.run.pipeline.name}”…")

        def done(_scenes):
            self._script_running = False
            self.retry_script_btn.setEnabled(True)
            self._sync_buttons()

        def fail(msg):
            self._script_running = False
            self.retry_script_btn.setEnabled(True)
            self._set_script_status(msg, is_error=True)
            self._sync_buttons()

        self.run.generate_breakdown(on_done=done, on_fail=fail)

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

    def _set_script_status(self, text: str, is_error: bool = False) -> None:
        from .. import theme
        self.script_status.setText(text)
        self.script_status.setStyleSheet(f"color:{theme.DANGER};" if is_error else "")

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
            row.jumpRequested.connect(self._jump_to_scene)
            self._rows[scene.id] = row
            self.list_lay.insertWidget(self.list_lay.count() - 1, row)

    def _jump_to_scene(self, scene_id: str):
        if not self.run:
            return
        clip_id = self.run.scene_current_clip_id(scene_id)
        if not clip_id or not self.win.timeline.reveal_clip(clip_id):
            self.status.emit("This scene hasn't been placed on the timeline yet.")
            return
        self.win.tabs.setCurrentIndex(0)   # Edit tab, where the timeline lives

    def _batch_limit(self) -> int | None:
        return self.batch_size_combo.currentData()

    def _relabel_stage_button(self, btn: QPushButton, icon: str, noun: str, total: int,
                              remaining: int, stage_label: str) -> None:
        """Shared by the images/video buttons: 'Generate' before anything in
        this stage exists, 'Continue (N left)' once some scenes have it but
        not all, disabled with a checkmark once the whole stage is done -
        the dynamic labeling IS the "continue where I left off" affordance,
        no separate resume button needed since Generate/Continue always
        only targets scenes still missing this stage."""
        done = total - remaining
        if total == 0 or done == 0:
            btn.setText(f"{icon} Generate scene {noun}")
            btn.setToolTip(f"{stage_label} — queues one {noun[:-1]}-generation call per scene, "
                           "honoring the batch size above.")
        elif remaining == 0:
            btn.setText(f"{icon} ✓ All scene {noun} generated")
            btn.setToolTip(f"Every scene already has {noun} - use a scene row's 🔄 to regenerate "
                           "an individual one.")
        else:
            btn.setText(f"{icon} Continue {noun} ({remaining} left)")
            btn.setToolTip(f"{stage_label} — {done}/{total} scenes done. Queues the next batch "
                           "of scenes still missing this stage, honoring the batch size above.")

    def _sync_buttons(self):
        has_run = self.run is not None
        p = self.run.pipeline if has_run else None
        if p:
            self.summary.setText(f"“{p.name}” · {len(p.scenes)} scene(s) · status: {p.status}")
        else:
            self.summary.setText("No movie loaded yet — click “New movie…” to describe one.")
        busy = bool(p and p.status in BUSY_STATUSES)
        total = len(p.scenes) if p else 0
        images_remaining = sum(1 for s in p.scenes if s.image.active is None) if p else 0
        video_remaining = sum(1 for s in p.scenes if s.video.active is None) if p else 0
        self._relabel_stage_button(self.images_btn, "🖼", "images", total, images_remaining,
                                   "Stage 1 of 2")
        self._relabel_stage_button(self.video_btn, "🎥", "video", total, video_remaining,
                                   "Stage 2 of 2 (most expensive)")
        self.images_btn.setEnabled(bool(p and p.scenes) and images_remaining > 0 and not busy)
        self.video_btn.setEnabled(bool(p and p.scenes and any(s.image.active for s in p.scenes))
                                  and video_remaining > 0 and not busy)
        needs_script = bool(p and not p.scenes)
        self.retry_script_btn.setVisible(needs_script)
        self.retry_script_btn.setEnabled(needs_script and not self._script_running)
        if needs_script and not self._script_running and not self.script_status.text():
            # A freshly-loaded saved pipeline that never got a working
            # breakdown - give the retry button a reason to exist rather
            # than leaving it unexplained.
            self._set_script_status("No scenes yet - the script breakdown hasn't run "
                                    "(or didn't finish) for this movie.")
        elif not needs_script:
            self.script_status.setText("")

    @staticmethod
    def _batch_wording(remaining: int, limit: int | None) -> str:
        """A phrase describing how many scenes will actually be queued -
        shared by both gate confirmations so a limited batch's dialog says
        "3 of 12" rather than the misleading full remaining count."""
        n = remaining if limit is None else min(remaining, limit)
        return f"all {n} remaining" if n == remaining else f"{n} of {remaining} remaining"

    def _stage_targets(self, stage: str, limit: int | None) -> list:
        """Same filter/sort/limit logic run_image_batch/run_video_batch use
        internally, replicated here (rather than exposed from the
        orchestrator) purely to price the exact scenes a confirm gate is
        about to queue - queries only, submits nothing."""
        active = "image" if stage == "image" else "video"
        scenes = sorted((s for s in self.run.pipeline.scenes
                        if getattr(s, active).active is None), key=lambda s: s.index)
        return scenes if limit is None else scenes[:limit]

    def _estimate_batch_cost(self, pipeline_model_key: str, targets: list, stage: str) -> float | None:
        """Total estimated $ across `targets`, honoring each scene's own
        model choice (falling back to the pipeline default, same rule the
        orchestrator itself uses) and param overrides (video length in
        particular varies scene-to-scene). None if pricing for any scene's
        model isn't known - callers should omit the cost from their message
        entirely rather than show a partial/misleading $0.00."""
        model_attr = "image_model" if stage == "image" else "video_model"
        overrides_attr = "image_params" if stage == "image" else "video_params"
        total = 0.0
        for scene in targets:
            model = self.registry.by_key(getattr(scene, model_attr) or pipeline_model_key)
            if not model:
                return None
            params = {**model.default_params(), **getattr(scene, overrides_attr)}
            cost = cost_estimator.estimate_cost(model, params)
            if cost is None:
                return None
            total += cost
        return total

    @staticmethod
    def _cost_phrase(cost: float | None) -> str:
        return "" if cost is None else f" (est. ${cost:,.2f})"

    # ---------------------------------------------------------------- gates
    def _run_images(self):
        if not self.run:
            return
        if not self.run.pipeline.scenes:
            self.status.emit("No scenes to generate images for yet - the script breakdown "
                             "hasn't produced any scenes.")
            return
        remaining = sum(1 for s in self.run.pipeline.scenes if s.image.active is None)
        if remaining == 0:
            self.status.emit("Every scene already has an image.")
            return
        limit = self._batch_limit()
        phrase = self._batch_wording(remaining, limit)
        targets = self._stage_targets("image", limit)
        cost_phrase = self._cost_phrase(
            self._estimate_batch_cost(self.run.pipeline.image_model, targets, "image"))
        if not confirm_destructive(
                self.win, self.settings, "pipeline_run_image_batch",
                "Generate scene images",
                f"This queues {phrase} scene(s) for image generation{cost_phrase}, using your "
                "configured image model. Each call spends your own API credits. Continue?",
                "Generate"):
            return
        self.run.run_image_batch(limit=limit)

    def _run_video(self):
        if not self.run:
            return
        if not self.run.pipeline.scenes:
            self.status.emit("No scenes to generate video for yet - the script breakdown "
                             "hasn't produced any scenes.")
            return
        remaining = sum(1 for s in self.run.pipeline.scenes if s.video.active is None)
        if remaining == 0:
            self.status.emit("Every scene already has a video.")
            return
        limit = self._batch_limit()
        phrase = self._batch_wording(remaining, limit)
        targets = self._stage_targets("video", limit)
        cost_phrase = self._cost_phrase(
            self._estimate_batch_cost(self.run.pipeline.video_model, targets, "video"))
        lipsync_note = (" plus a lip-sync pass" if self.run.pipeline.lipsync_model else "")
        if not confirm_destructive(
                self.win, self.settings, "pipeline_run_video_batch",
                "Generate scene video",
                f"This queues {phrase} scene(s) for video generation{cost_phrase}"
                f"{lipsync_note} — the most expensive stage. Each call spends your own API "
                "credits. Continue?",
                "Generate"):
            return
        self.run.run_video_batch(limit=limit)
