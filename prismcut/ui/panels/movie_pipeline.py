"""Movie Pipeline panel: describe-a-movie -> scripted, storyboarded, voiced,
generated scenes, laid out on the timeline automatically. The deterministic
stage sequence itself lives in core.pipeline_orchestrator.PipelineRun; this
panel presents it as an explicit Inputs/Script/Generate/Scenes/Assemble
tabbed pipeline (self.stage_tabs) - every tab freely clickable, no stage-
gating, matching the only embedded-QTabWidget precedent this app has
elsewhere (audio_panel.py, nano_tools.py, prompt_lab.py). The Jobs dock
stays the single progress source of truth (per-scene rows show only a
status icon, click it to raise Jobs), matching how main_window.py already
raises the Effects dock on clip selection rather than duplicating progress
UI locally."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QHBoxLayout, QInputDialog, QLabel,
                               QPlainTextEdit, QPushButton, QScrollArea, QTabWidget,
                               QTextBrowser, QVBoxLayout, QWidget)

from ...core import cost_estimator
from ...core import media as media_utils
from ...core import paths
from ...core.pipeline import MoviePipeline, Scene
from ...core.pipeline_orchestrator import PipelineRun, _append_lyric_guidance
from ...providers.base import ChatMessage
from ..dialogs.new_pipeline_dialog import NewPipelineDialog
from ..widgets.common import (STATUS_ICONS, CollapsibleSection, DropAcceptor, ModelCombo,
                              TakeFilmstrip, accent_button, confirm_destructive, label,
                              sync_text_edit)
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


def _scene_status_color(scene: Scene) -> str:
    from .. import theme
    if scene.last_error:
        return theme.DANGER
    if scene.video.active:
        return theme.ACCENT
    if scene.image.active:
        return theme.ORANGE
    return theme.TEXT_DIM


class SceneStatusStrip(QWidget):
    """Compact horizontal per-scene status strip - one small waveform
    thumbnail per scene (that scene's own narration audio, once generated)
    with a colored border for status. PrismCut's pipeline is narration-
    driven (each scene gets its own short TTS clip), not song-driven like
    the reference product this was inspired by (no "upload one song for
    the whole movie" concept exists here) - so this shows a SEQUENCE of
    scene-owned waveforms rather than one continuous track split into
    colored regions.

    Status is a colored BORDER around a fixed-color waveform image, not
    recolored waveform pixels - media.waveform_png()'s on-disk cache is
    keyed by (path, size, color), so recoloring the waveform itself on
    every status change would mean re-invoking ffmpeg over and over for
    the exact same audio just to change its tint. A border achieves the
    same at-a-glance goal without that churn."""

    CARD_SIZE = (84, 54)

    def __init__(self, panel: "MoviePipelinePanel", parent=None):
        super().__init__(parent)
        self.panel = panel
        self._cards: dict[str, QLabel] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.row_host = QWidget()
        self.row_lay = QHBoxLayout(self.row_host)
        self.row_lay.setContentsMargins(0, 0, 0, 0)
        self.row_lay.setSpacing(4)
        self.row_lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(self.row_host)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setFixedHeight(self.CARD_SIZE[1] + 14)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

    def rebuild(self) -> None:
        for card in self._cards.values():
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()
        run = self.panel.run
        if not run:
            return
        for scene in sorted(run.pipeline.scenes, key=lambda s: s.index):
            card = QLabel()
            card.setFixedSize(*self.CARD_SIZE)
            card.setAlignment(Qt.AlignmentFlag.AlignCenter)
            card.setScaledContents(True)
            self._cards[scene.id] = card
            self.row_lay.insertWidget(self.row_lay.count() - 1, card)
        self.sync_all()

    def sync_all(self) -> None:
        if not self.panel.run:
            return
        for scene in self.panel.run.pipeline.scenes:
            self.sync_one(scene.id)

    def sync_one(self, scene_id: str) -> None:
        run = self.panel.run
        card = self._cards.get(scene_id) if run else None
        if not card:
            return
        scene = run.pipeline.scene(scene_id)
        if not scene:
            return
        aud = scene.audio.active
        item = run.win.project.media.get(aud.media_id) if aud else None
        wf = media_utils.waveform_png(item.path, width=self.CARD_SIZE[0] * 2,
                                      height=self.CARD_SIZE[1] * 2) if item else None
        card.setPixmap(QPixmap(str(wf)) if wf else QPixmap())
        card.setStyleSheet(f"border:2px solid {_scene_status_color(scene)};border-radius:4px;"
                           "background:rgba(127,127,127,30);")
        card.setToolTip(f"Scene {scene.index + 1}" + (f" — {scene.last_error}"
                        if scene.last_error else ""))


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
        self.script_edit.textChanged.connect(self._update_char_count)
        v.addWidget(self.script_edit)

        char_row = QHBoxLayout()
        self.char_count_label = label("", dim=True)
        char_row.addWidget(self.char_count_label, 1)
        copy_prompt_btn = QPushButton("📋 Copy generation prompt")
        copy_prompt_btn.setToolTip(
            "Copies the actual text that would be sent for generation - this scene's script "
            "(or the movie's brief, if the script is still blank) plus any lyric guidance "
            "from transcribed captions, if this scene has any.")
        copy_prompt_btn.clicked.connect(self._copy_generation_prompt)
        char_row.addWidget(copy_prompt_btn)
        v.addLayout(char_row)
        self._update_char_count()

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

        self.image_takes_label = label("Past image takes — click to switch", dim=True)
        v.addWidget(self.image_takes_label)
        self.image_takes = TakeFilmstrip()
        self.image_takes.takeSelected.connect(self._image_take_selected)
        v.addWidget(self.image_takes)

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

        self.video_takes_label = label("Past video takes — click to switch", dim=True)
        v.addWidget(self.video_takes_label)
        self.video_takes = TakeFilmstrip()
        self.video_takes.takeSelected.connect(self._video_take_selected)
        v.addWidget(self.video_takes)

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
        # Emitting (rather than calling self.sync() directly) also refreshes
        # ScriptSceneRow's view of the same scene in the Script tab - two
        # widgets bound to the same Scene should both see either one's save,
        # not just the one that made it. Direct/same-thread connections
        # deliver synchronously, so this row's own sync() still happens
        # before this method returns, same as before.
        self.run.sceneChanged.emit(self.scene.id)
        self.run.win.toast(f"Scene {self.scene.index + 1} changes saved.", "success")

    def _on_scene_changed(self, scene_id: str):
        if scene_id == self.scene.id:
            self.sync()

    def _update_char_count(self) -> None:
        n = len(self.script_edit.toPlainText())
        self.char_count_label.setText(f"{n:,} char{'s' if n != 1 else ''}")

    def _copy_generation_prompt(self) -> None:
        prompt = self.script_edit.toPlainText().strip() or self.run.pipeline.brief
        prompt = _append_lyric_guidance(prompt, self.scene)
        QApplication.clipboard().setText(prompt)
        self.run.win.toast("✓ Copied this scene's generation prompt.", "success")

    def _image_take_selected(self, index: int):
        self.run.set_active_take(self.scene.id, "image", index)

    def _video_take_selected(self, index: int):
        self.run.set_active_take(self.scene.id, "video", index)

    def _sync_take_filmstrip(self, history, filmstrip: TakeFilmstrip, label_widget,
                             base_text: str) -> None:
        # Hidden for the common single-take case - browsing has nothing to
        # offer until there's actually more than one take to pick between,
        # and this Details section is already fairly busy.
        show = len(history.entries) > 1
        label_widget.setVisible(show)
        filmstrip.setVisible(show)
        if not show:
            return
        thumbs = []
        total_bytes = 0
        for entry in history.entries:
            item = self.run.win.project.media.get(entry.media_id) if entry.media_id else None
            thumbs.append(media_utils.thumbnail(item.path) if item else None)
            if item:
                try:
                    total_bytes += Path(item.path).stat().st_size
                except OSError:
                    pass   # a stale/offline reference just doesn't count toward the total
        filmstrip.set_entries(thumbs, history.current)
        mb = total_bytes / (1024 * 1024)
        label_widget.setText(f"{base_text} ({len(history.entries)} takes, ~{mb:.1f} MB)")

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
        sync_text_edit(self.script_edit, self.scene.script)
        self._sync_take_filmstrip(self.scene.image, self.image_takes, self.image_takes_label,
                                  "Past image takes — click to switch")
        self._sync_take_filmstrip(self.scene.video, self.video_takes, self.video_takes_label,
                                  "Past video takes — click to switch")

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


class ScriptSceneRow(QWidget):
    """Script tab's lightweight per-scene row: just the two fields that
    define what a scene actually IS (on-screen action + spoken narration) -
    no thumbnail, no generation controls, no model overrides, no take
    history. Deliberately a SEPARATE widget from SceneRow (Scenes tab),
    not a subset view of it - both are bound to the same Scene and both
    listen to the same sceneChanged signal, so SceneRow's existing
    "don't clobber a focused, mid-edit field" guard (now the shared
    sync_text_edit helper) has to be applied here too: an unrelated event
    (this scene's image finishing in the background, say) must not
    overwrite a user's in-progress keystrokes in the OTHER tab."""

    def __init__(self, run: PipelineRun, scene: Scene, parent=None):
        super().__init__(parent)
        self.run = run
        self.scene = scene

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 3, 4, 3)
        outer.setSpacing(2)

        self.title = label(f"Scene {scene.index + 1}", dim=True)
        outer.addWidget(self.title)

        outer.addWidget(label("Script / visual prompt", dim=True))
        self.script_edit = QPlainTextEdit(scene.script)
        self.script_edit.setMaximumHeight(70)
        self.script_edit.setPlaceholderText("Describe what happens in this scene…")
        outer.addWidget(self.script_edit)

        outer.addWidget(label("Narration / dialogue", dim=True))
        self.narration_edit = QPlainTextEdit(scene.narration)
        self.narration_edit.setMaximumHeight(50)
        self.narration_edit.setPlaceholderText(
            "Spoken narration for this scene - leave blank for silent…")
        outer.addWidget(self.narration_edit)

        save_btn = QPushButton("💾 Save")
        save_btn.setToolTip("Saves this scene's script and narration - applied next time it's "
                            "(re)generated.")
        save_btn.clicked.connect(self._save)
        outer.addWidget(save_btn)

        run.sceneChanged.connect(self._on_scene_changed)
        self.sync()

    def _on_scene_changed(self, scene_id: str):
        if scene_id == self.scene.id:
            self.sync()

    def _save(self):
        self.scene.script = self.script_edit.toPlainText()
        self.scene.narration = self.narration_edit.toPlainText()
        self.run.pipeline.save()
        # See SceneRow._save_details' identical comment - keeps the Scenes
        # tab's SceneRow for this same scene in sync too.
        self.run.sceneChanged.emit(self.scene.id)
        self.run.win.toast(f"Scene {self.scene.index + 1} changes saved.", "success")

    def sync(self):
        self.title.setText(f"Scene {self.scene.index + 1}")
        sync_text_edit(self.script_edit, self.scene.script)
        sync_text_edit(self.narration_edit, self.scene.narration)


class AspectRatioPickDialog(QDialog):
    """Prominent tile picker shown before Fire's cost confirm, when the
    pipeline's own default video model exposes aspect_ratio as a param -
    replaces having to open a scene's Details and hunt for the plain
    dropdown ParamForm already renders for it (unchanged, still there for
    per-scene overrides). One dialog, one job: pick a ratio or cancel."""

    def __init__(self, choices: list, current: str, overridden_count: int, parent=None):
        super().__init__(parent)
        self.overridden_count = overridden_count   # kept as a plain attribute for tests to inspect
        self.setWindowTitle("Choose aspect ratio")
        v = QVBoxLayout(self)
        v.addWidget(label("Applies to every scene using the pipeline's own default model:",
                          dim=True))
        row = QHBoxLayout()
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for choice in choices:
            btn = QPushButton(str(choice))
            btn.setCheckable(True)
            btn.setMinimumHeight(36)
            if str(choice) == current:
                btn.setChecked(True)
            self._group.addButton(btn)
            row.addWidget(btn)
        v.addLayout(row)
        if overridden_count:
            note = label(f"{overridden_count} scene(s) use a different model - set aspect "
                         "ratio in their own Details instead.", dim=True)
            v.addWidget(note)

        buttons = QDialogButtonBox()
        buttons.addButton("Use this", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

    def chosen(self) -> str | None:
        checked = self._group.checkedButton()
        return checked.text() if checked else None


class ProducerChatPanel(QWidget):
    """A conversational, multi-turn copilot for refining a movie's brief -
    Script tab. Deliberately built on this module's own existing
    jobs.submit() one-shot pattern (already used twice here: script
    breakdown, "Enhance brief (AI)") rather than ChatPanel's ChatWorker/
    QThread token-streaming mechanism, to avoid introducing a second,
    competing async pattern into an already-large change - busy state is
    "disable Send, show a status line," the same as "Enhance brief (AI)"
    already does, not a live streaming cursor.

    Scoped to proposing a revised BRIEF only (not structured per-scene
    edits - identifying which scenes and what changes from free-form text
    would need a much larger parsing scheme, a separable feature). HARD
    CONSTRAINT: this can only PROPOSE a rewritten brief for the user to
    review and explicitly Apply - it must never itself call
    run_image_batch/run_video_batch/_run_fire, matching this codebase's
    own repeated "deterministic, never agentic, for anything spending the
    user's API credits" principle (pipeline_orchestrator.py's own module
    docstring). A chat surface must not become a backdoor around that."""

    SUGGESTIONS = ("Make it more cinematic", "Add a twist ending",
                   "Shorten it", "Make the tone funnier")
    _PROPOSAL_MARKER = "PROPOSED BRIEF:"

    def __init__(self, panel: "MoviePipelinePanel", parent=None):
        super().__init__(parent)
        self.panel = panel
        self._messages: list[ChatMessage] = []
        self._sending = False
        self._pending_proposal = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(4)

        outer.addWidget(label("🎬 Producer — chat to refine the brief", dim=True))

        self.transcript = QTextBrowser()
        self.transcript.setMinimumHeight(90)
        self.transcript.setMaximumHeight(160)
        outer.addWidget(self.transcript)

        chips_row = QHBoxLayout()
        for text in self.SUGGESTIONS:
            btn = QPushButton(text)
            btn.setStyleSheet("text-align:left;padding:4px 8px;")
            btn.clicked.connect(lambda _checked=False, t=text: self._use_suggestion(t))
            chips_row.addWidget(btn)
        outer.addLayout(chips_row)

        input_row = QHBoxLayout()
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("Ask the Producer to revise the brief…")
        self.input.setMaximumHeight(56)
        input_row.addWidget(self.input, 1)
        self.send_btn = QPushButton("Send ➤")
        self.send_btn.clicked.connect(self._send)
        input_row.addWidget(self.send_btn)
        outer.addLayout(input_row)

        self.proposal_label = label("", dim=True)
        self.proposal_label.setWordWrap(True)
        self.proposal_label.setVisible(False)
        outer.addWidget(self.proposal_label)
        self.apply_btn = QPushButton("✓ Apply to brief")
        self.apply_btn.setVisible(False)
        self.apply_btn.clicked.connect(self._apply_proposal)
        outer.addWidget(self.apply_btn)

    def reset(self) -> None:
        """Clears all conversation state - called whenever a different
        pipeline gets loaded (MoviePipelinePanel._set_pipeline), so
        switching movies doesn't carry an unrelated chat history/context
        (or a pending proposal meant for the OLD movie's brief) into the
        newly-loaded one."""
        self._messages = []
        self._pending_proposal = ""
        self.transcript.clear()
        self.proposal_label.setVisible(False)
        self.apply_btn.setVisible(False)
        self.input.clear()

    def _use_suggestion(self, text: str) -> None:
        self.input.setPlainText(text)
        self._send()

    def _send(self) -> None:
        if self._sending:
            return
        text = self.input.toPlainText().strip()
        run = self.panel.run
        if not text or not run:
            return
        model = self.panel.registry.by_key(run.pipeline.script_model)
        if not model:
            self._append_transcript("Producer", "No script model is configured for this movie.")
            return
        self._messages.append(ChatMessage("user", text))
        self._append_transcript("You", text)
        self.input.clear()
        self._set_sending(True)

        adapter = self.panel.win.get_adapter(model.provider)
        sys_prompt = (
            "You are a movie producer helping refine a brief for an AI video-generation "
            "pipeline. The CURRENT brief is:\n\n" + (run.pipeline.brief or "(empty)") +
            "\n\nDiscuss the requested change conversationally. If you want to propose a "
            f"concrete revised brief, end your reply with a line reading exactly "
            f"'{self._PROPOSAL_MARKER}' followed by the full rewritten brief text on the "
            "lines after it - keep every plot beat the user didn't ask to change. If you're "
            "just answering a question or need clarification, don't include that line at "
            "all.")
        history = list(self._messages)

        def work(job):
            job.progress(-1, "Producer is thinking…")
            return adapter.chat(model.id, history, system=sys_prompt, temperature=0.6)

        def done(result):
            self._set_sending(False)
            text_out = str(result)
            reply, proposal = self._split_proposal(text_out)
            self._messages.append(ChatMessage("assistant", text_out))
            self._append_transcript("Producer", reply or "(no reply text)")
            if proposal:
                self._show_proposal(proposal)

        def fail(msg):
            self._set_sending(False)
            self._append_transcript("Producer", f"(failed: {msg})")

        self.panel.win.jobs.submit("Producer chat", work, kind="chat", on_done=done, on_fail=fail)

    @classmethod
    def _split_proposal(cls, text: str) -> tuple:
        idx = text.find(cls._PROPOSAL_MARKER)
        if idx == -1:
            return text.strip(), ""
        return text[:idx].strip(), text[idx + len(cls._PROPOSAL_MARKER):].strip()

    def _show_proposal(self, proposal: str) -> None:
        self._pending_proposal = proposal
        self.proposal_label.setText(f"Proposed brief:\n{proposal}")
        self.proposal_label.setVisible(True)
        self.apply_btn.setVisible(True)

    def _apply_proposal(self) -> None:
        run = self.panel.run
        if not self._pending_proposal or not run:
            return
        run.pipeline.brief = self._pending_proposal
        run.pipeline.save()
        self.proposal_label.setVisible(False)
        self.apply_btn.setVisible(False)
        self._pending_proposal = ""
        self.panel.win.toast("Applied the Producer's revised brief.", "success")
        self.panel._sync_buttons()

    def _append_transcript(self, who: str, text: str) -> None:
        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.transcript.append(f"<b>{who}:</b> {safe}")

    def _set_sending(self, sending: bool) -> None:
        self._sending = sending
        self.send_btn.setEnabled(not sending)
        self.send_btn.setText("Sending…" if sending else "Send ➤")


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
        self._script_rows: dict[str, ScriptSceneRow] = {}
        self._script_running = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # Persistent header, visible regardless of which stage tab is active -
        # "which movie, how far along" is worth seeing no matter what you're
        # currently doing with it.
        self.summary = label("No movie loaded yet — click “New movie…” to describe one.",
                             dim=True)
        outer.addWidget(self.summary)

        # Explicit stage-by-stage pipeline (Inputs -> Script -> Generate ->
        # Scenes -> Assemble), replacing the old single scrolling panel -
        # every tab is always clickable (no "tab N unlocks after tab N-1"
        # gating - this codebase's other embedded QTabWidgets, e.g.
        # audio_panel.py/nano_tools.py/prompt_lab.py, are all flat/ungated
        # too, and there's no stepper/wizard precedent anywhere to build
        # gating logic on). Named "Scenes," not "Edit," specifically to
        # avoid colliding with the app's own outer central tab already named
        # "🎬  Edit" (main_window.py) - two same-named tabs at two nesting
        # levels would be a real source of confusion.
        self.stage_tabs = QTabWidget()
        outer.addWidget(self.stage_tabs, 1)

        self.stage_tabs.addTab(self._build_inputs_tab(), "📥 Inputs")
        self.stage_tabs.addTab(self._build_script_tab(), "📝 Script")
        self.stage_tabs.addTab(self._build_generate_tab(), "🎬 Generate")
        self._scenes_tab = self._build_scenes_tab()
        self.stage_tabs.addTab(self._scenes_tab, "🎞 Scenes")
        self.stage_tabs.addTab(self._build_assemble_tab(), "✅ Assemble")

        self._refresh_load_combo()
        self._sync_buttons()

    def show_scenes_tab(self) -> None:
        """Public so callers outside this panel (main_window.py's timeline-
        driven "Regenerate this scene" action) can land the user on the
        actual scene list, not whichever inner tab happened to be active
        last - a regenerating row's status icon/error label only live here."""
        self.stage_tabs.setCurrentWidget(self._scenes_tab)

    def _build_inputs_tab(self) -> QWidget:
        host = QWidget()
        v = QVBoxLayout(host)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(6)
        top = QHBoxLayout()
        new_btn = accent_button("🎬 New movie…")
        new_btn.clicked.connect(self.new_pipeline)
        self.load_combo = QComboBox()
        self.load_combo.setMinimumWidth(220)
        self.load_combo.activated.connect(self._load_selected)
        top.addWidget(new_btn)
        top.addWidget(self.load_combo, 1)
        v.addLayout(top)
        v.addStretch(1)
        return host

    def _build_script_tab(self) -> QWidget:
        host = QWidget()
        v = QVBoxLayout(host)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(6)
        # Persistent (not just a 5s toast) record of the last script-breakdown
        # attempt - a failure here (bad JSON, provider declined the brief,
        # network error) used to be visible only as a transient toast, easy
        # to miss, after which the panel just sat there with 0 scenes and no
        # visible explanation.
        self.script_status = label("", dim=True)
        self.script_status.setWordWrap(True)
        v.addWidget(self.script_status)

        self.retry_script_btn = QPushButton("🔄 Retry script breakdown")
        self.retry_script_btn.setToolTip(
            "Re-sends the same brief to the script model. Useful if the last attempt failed "
            "or the AI declined to break it down.")
        self.retry_script_btn.clicked.connect(self._retry_script)
        self.retry_script_btn.setVisible(False)
        v.addWidget(self.retry_script_btn)

        self.producer_chat = ProducerChatPanel(self)
        v.addWidget(self.producer_chat)

        self.script_list_host = QWidget()
        self.script_list_lay = QVBoxLayout(self.script_list_host)
        self.script_list_lay.setContentsMargins(0, 0, 0, 0)
        self.script_list_lay.setSpacing(1)
        self.script_list_lay.addStretch(1)
        script_scroll = QScrollArea()
        script_scroll.setWidget(self.script_list_host)
        script_scroll.setWidgetResizable(True)
        script_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        v.addWidget(script_scroll, 1)
        return host

    def _build_generate_tab(self) -> QWidget:
        host = QWidget()
        v = QVBoxLayout(host)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(6)

        self.generate_status_strip = SceneStatusStrip(self)
        v.addWidget(self.generate_status_strip)

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
        size_row.addWidget(label("Concurrent video:", dim=True))
        self.concurrency_combo = QComboBox()
        self.concurrency_combo.addItem("1 (sequential)", 1)
        self.concurrency_combo.addItem("2 at once", 2)
        self.concurrency_combo.addItem("3 at once", 3)
        self.concurrency_combo.addItem("5 at once", 5)
        self.concurrency_combo.setToolTip(
            "How many scenes' video generation run at once (during the video stage below, "
            "or 🔫 Fire) - higher finishes faster but is more likely to trip a provider's "
            "own rate limit, so lower this again if generations start failing under load. "
            "Image generation always runs one scene at a time regardless of this setting - "
            "each scene's image uses earlier scenes as a visual-continuity reference, so it "
            "can't be reordered or parallelized the way video can.")
        self.concurrency_combo.currentIndexChanged.connect(self._concurrency_changed)
        size_row.addWidget(self.concurrency_combo)
        size_row.addStretch(1)
        v.addLayout(size_row)

        stage_row = QHBoxLayout()
        self.images_btn = accent_button("🖼 Generate scene images")
        self.images_btn.clicked.connect(self._run_images)
        self.video_btn = accent_button("🎥 Generate scene video")
        self.video_btn.clicked.connect(self._run_video)
        stage_row.addWidget(self.images_btn)
        stage_row.addWidget(self.video_btn)
        v.addLayout(stage_row)

        fire_row = QHBoxLayout()
        self.fire_btn = accent_button("🔫 Fire — finish this movie")
        self.fire_btn.setToolTip(
            "Runs whatever's left across both stages above, back to back, behind one "
            "combined cost confirmation - ignores the batch size above and always targets "
            "every remaining scene. The two stage buttons above still work exactly as "
            "before if you'd rather review images before committing to video.")
        self.fire_btn.clicked.connect(self._run_fire)
        fire_row.addWidget(self.fire_btn)
        v.addLayout(fire_row)
        v.addStretch(1)
        return host

    def _build_scenes_tab(self) -> QWidget:
        host = QWidget()
        v = QVBoxLayout(host)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(6)

        self.scenes_status_strip = SceneStatusStrip(self)
        v.addWidget(self.scenes_status_strip)

        self.list_host = QWidget()
        self.list_lay = QVBoxLayout(self.list_host)
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self.list_lay.setSpacing(1)
        self.list_lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(self.list_host)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        v.addWidget(scroll, 1)
        return host

    def _build_assemble_tab(self) -> QWidget:
        host = QWidget()
        v = QVBoxLayout(host)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(6)
        self.assemble_status = label("No movie loaded yet.", dim=True)
        self.assemble_status.setWordWrap(True)
        v.addWidget(self.assemble_status)

        self.burn_in_captions_check = QCheckBox("🔤 Burn captions into the final export")
        self.burn_in_captions_check.setToolTip(
            "Renders each scene's transcribed narration as on-screen text in the final "
            "export. Independent of the auto-captions setting chosen when this movie was "
            "created - that controls whether the transcript DATA exists at all (feeding "
            "video-prompt guidance either way); this only controls whether it's also drawn "
            "onto the video. Off by default - existing caption data shouldn't silently "
            "change what an export looks like unless you ask for it. No effect on scenes "
            "with no transcript.")
        self.burn_in_captions_check.toggled.connect(self._burn_in_captions_toggled)
        v.addWidget(self.burn_in_captions_check)

        export_btn = accent_button("📤 Export…")
        export_btn.setToolTip("Opens the app's Export dialog to render your project - the "
                              "same Export any other timeline content uses.")
        export_btn.clicked.connect(self.win.export_dialog)
        v.addWidget(export_btn)
        v.addStretch(1)
        return host

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
        self.producer_chat.reset()
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

    def _on_scene_list_maybe_changed(self, scene_id: str):
        # A scene's first asset landing can be the moment scenes go from "not
        # rendered as rows yet" (right after generate_breakdown) to needing
        # rows - cheapest correct check is just comparing row count to scene
        # count rather than tracking that transition explicitly.
        if self.run and len(self._rows) != len(self.run.pipeline.scenes):
            self._rebuild_scene_rows()
        else:
            # scene count unchanged - an existing scene's status changed
            # (image/video landed, an error was set, ...), so the status
            # strips just need this one card refreshed, not a full rebuild.
            self.generate_status_strip.sync_one(scene_id)
            self.scenes_status_strip.sync_one(scene_id)
        self._sync_buttons()

    def _rebuild_scene_rows(self):
        """Rebuilds the Scenes tab's SceneRow list, the Script tab's
        ScriptSceneRow list, AND both status strips together - one trigger
        for all four, since they're always driven by the exact same
        condition (the pipeline's own scene list changed shape), rather
        than independent rebuild checks that could in principle disagree
        over what "changed" means."""
        for row in self._rows.values():
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()
        for row in self._script_rows.values():
            row.setParent(None)
            row.deleteLater()
        self._script_rows.clear()
        self.generate_status_strip.rebuild()
        self.scenes_status_strip.rebuild()
        if not self.run:
            return
        for scene in sorted(self.run.pipeline.scenes, key=lambda s: s.index):
            row = SceneRow(self.run, scene)
            row.jobsRequested.connect(self.jobsRequested)
            row.jumpRequested.connect(self._jump_to_scene)
            self._rows[scene.id] = row
            self.list_lay.insertWidget(self.list_lay.count() - 1, row)

            script_row = ScriptSceneRow(self.run, scene)
            self._script_rows[scene.id] = script_row
            self.script_list_lay.insertWidget(self.script_list_lay.count() - 1, script_row)

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

    def _concurrency_changed(self, _idx: int = 0):
        # Unlike batch size (transient UI-only state), concurrency is
        # persisted per-pipeline - Fire and a later session's "Continue"
        # click should both keep using whatever this movie was configured
        # with, not silently reset to some UI default.
        if self.run:
            self.run.pipeline.video_concurrency = self.concurrency_combo.currentData()
            self.run.pipeline.save()

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
        if p:
            self.concurrency_combo.blockSignals(True)
            idx = self.concurrency_combo.findData(p.video_concurrency)
            self.concurrency_combo.setCurrentIndex(idx if idx >= 0 else 0)
            self.concurrency_combo.blockSignals(False)
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
        # Fire doesn't share video_btn's "at least one image already exists"
        # precondition - it always does images-first itself (see _run_fire),
        # so it's meaningful the moment scenes exist and either stage has
        # anything left, even before a single image has ever been generated.
        self.fire_btn.setEnabled(bool(p and p.scenes) and (images_remaining > 0 or video_remaining > 0)
                                 and not busy)
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
        self._sync_assemble_status(p, total, images_remaining, video_remaining)

    def _burn_in_captions_toggled(self, checked: bool) -> None:
        if self.run:
            self.run.pipeline.burn_in_captions = checked
            self.run.sync_caption_burn_in()
            self.run.pipeline.save()

    def _sync_assemble_status(self, p: MoviePipeline | None, total: int,
                              images_remaining: int, video_remaining: int) -> None:
        if p:
            self.burn_in_captions_check.blockSignals(True)
            self.burn_in_captions_check.setChecked(p.burn_in_captions)
            self.burn_in_captions_check.blockSignals(False)
        if not p:
            self.assemble_status.setText("No movie loaded yet.")
            return
        if not p.scenes:
            self.assemble_status.setText("No scenes yet - finish the Script stage first.")
            return
        if video_remaining == 0:
            self.assemble_status.setText(
                f"“{p.name}” is complete - all {total} scene(s) have video. Export below when "
                "you're ready.")
        else:
            self.assemble_status.setText(
                f"“{p.name}”: {total - images_remaining}/{total} scene(s) have images, "
                f"{total - video_remaining}/{total} have video. Finish in the Generate tab.")

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

    def _maybe_pick_aspect_ratio(self, image_targets: list, video_targets: list) -> None:
        """Shows AspectRatioPickDialog before Fire's cost confirm, when the
        pipeline's own default video model exposes aspect_ratio as a param
        (most video models do; Sora/GPT-Image use an unrelated `size` param
        instead and get no picker here at all - ParamForm's own generic
        dropdown, unaffected by this method, is still there for anyone
        setting one by hand). Only ever writes onto scenes using the
        pipeline's default model for BOTH stages - a single Fire click can
        span scenes on genuinely different models with different choice
        sets, so a scene with its own per-scene override keeps whatever its
        own Details section already has instead of being silently
        overwritten by a picker keyed off a model it isn't even using."""
        pipeline = self.run.pipeline
        model = self.registry.by_key(pipeline.video_model)
        spec = next((p for p in (model.params if model else [])
                    if p.get("name") == "aspect_ratio"), None)
        if not spec or not spec.get("choices"):
            return
        targets_by_id = {s.id: s for s in image_targets + video_targets}
        all_targets = list(targets_by_id.values())
        default_scenes = [s for s in all_targets if not s.image_model and not s.video_model]
        if not default_scenes:
            return
        overridden_count = len(all_targets) - len(default_scenes)
        current = str(default_scenes[0].video_params.get("aspect_ratio")
                      or spec.get("default") or spec["choices"][0])
        dlg = AspectRatioPickDialog(spec["choices"], current, overridden_count, self.win)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        chosen = dlg.chosen()
        if not chosen:
            return
        for scene in default_scenes:
            scene.image_params["aspect_ratio"] = chosen
            scene.video_params["aspect_ratio"] = chosen
        self.run.pipeline.save()

    def _estimate_lipsync_cost(self, video_targets: list) -> float | None:
        """Lip-sync pricing for Fire's combined estimate. The two existing
        per-stage buttons have never priced this at all - a pre-existing
        gap that silently omits lip-sync cost from their estimates whenever
        pipeline.lipsync_model is configured - worth fixing here since
        Fire's whole pitch is one honest combined number. Returns 0.0 (not
        None) when there's nothing to price (no lip-sync model configured,
        or no target scene has use_lipsync set) - only None when a
        configured model genuinely can't be priced, matching
        _estimate_batch_cost's own None convention."""
        lipsync_key = self.run.pipeline.lipsync_model
        if not lipsync_key:
            return 0.0
        scenes = [s for s in video_targets if s.use_lipsync]
        if not scenes:
            return 0.0
        model = self.registry.by_key(lipsync_key)
        if not model:
            return None
        total = 0.0
        for scene in scenes:
            duration = (scene.video_params.get("duration")
                       or self.run.pipeline.default_scene_duration or None)
            cost = cost_estimator.estimate_cost(model, model.default_params(),
                                                duration_seconds=duration)
            if cost is None:
                return None
            total += cost
        return total

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

    def _run_fire(self):
        """One-click path: runs whatever's left across BOTH stages, back to
        back, behind a single combined confirm - composes the two existing
        batch methods via run_image_batch's own on_all_done parameter, so
        if images are already fully done it correctly degrades to "just run
        video" (run_image_batch finds zero targets and calls on_all_done()
        immediately). Deliberately ignores the "Batch size" limiter above
        and always targets every remaining scene in both stages: under a
        small limit, the image and video sub-batches aren't guaranteed to
        target the same scenes (video's own "first N missing, by index"
        filter can pick entirely different scenes than images just
        generated), which would make one combined confirm dialog actively
        misleading. The two stage buttons above are untouched and still
        honor the batch size, for anyone who wants that finer control."""
        if not self.run:
            return
        if not self.run.pipeline.scenes:
            self.status.emit("No scenes to generate yet - the script breakdown hasn't "
                             "produced any scenes.")
            return
        image_targets = self._stage_targets("image", None)
        video_targets = self._stage_targets("video", None)
        if not image_targets and not video_targets:
            self.status.emit("Every scene already has both an image and a video.")
            return
        # Optional - cancelling this sub-step just leaves the aspect ratio
        # as whatever it already was and continues on to the cost confirm
        # below, it doesn't abort the whole Fire action.
        self._maybe_pick_aspect_ratio(image_targets, video_targets)
        pipeline = self.run.pipeline
        parts = []
        total = 0.0
        any_unknown = False
        if image_targets:
            cost = self._estimate_batch_cost(pipeline.image_model, image_targets, "image")
            parts.append(f"Images: {self._batch_wording(len(image_targets), None)}"
                         f"{self._cost_phrase(cost)}")
            any_unknown = any_unknown or cost is None
            total += cost or 0.0
        if video_targets:
            cost = self._estimate_batch_cost(pipeline.video_model, video_targets, "video")
            parts.append(f"Video: {self._batch_wording(len(video_targets), None)}"
                         f"{self._cost_phrase(cost)}")
            any_unknown = any_unknown or cost is None
            total += cost or 0.0
            lip_cost = self._estimate_lipsync_cost(video_targets)
            any_unknown = any_unknown or lip_cost is None
            if lip_cost:
                parts.append(f"lip-sync ~${lip_cost:,.2f}")
                total += lip_cost
        total_phrase = "" if any_unknown else f" — combined est. ${total:,.2f}"
        message = (
            f"This finishes the whole movie: {'; '.join(parts)}{total_phrase}. Each call "
            "spends your own API credits - this estimate doesn't include the cost of an "
            "automatic retry if a scene is rejected by moderation. Continue?")
        if not confirm_destructive(
                self.win, self.settings, "pipeline_run_fire_batch",
                "Fire — generate everything remaining", message, "Fire"):
            return
        self.run.run_image_batch(on_all_done=self.run.run_video_batch)
