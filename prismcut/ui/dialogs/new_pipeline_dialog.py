"""New movie dialog: one brief + one model picker per pipeline stage."""
from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QPushButton)

from ...core import cost_estimator
from ...core import media as media_utils
from ...core.pipeline import MoviePipeline
from ...providers.base import ChatMessage
from ..widgets.common import DropAcceptor, ModelCombo, label


class NewPipelineDialog(QDialog):
    def __init__(self, registry, settings, jobs, get_adapter, parent=None):
        super().__init__(parent)
        self.jobs = jobs
        self.get_adapter = get_adapter
        self.setWindowTitle("New movie")
        self.resize(560, 500)
        form = QFormLayout(self)

        self.name_edit = QLineEdit("Untitled movie")
        form.addRow("Name", self.name_edit)

        self.brief_edit = QPlainTextEdit()
        self.brief_edit.setPlaceholderText(
            "Describe the whole show/movie start to finish - setting, characters, plot "
            "beats, tone, desired length. The AI breaks this into a numbered, scene-by-"
            "scene shot list (visual description + narration/dialogue per scene).")
        self.brief_edit.setMinimumHeight(130)
        form.addRow("Brief", self.brief_edit)

        erow = QHBoxLayout()
        self.enhance_btn = QPushButton("✨ Enhance brief (AI)")
        self.enhance_btn.setToolTip(
            "Rewrites your brief with richer cinematic detail - settings, blocking, camera, "
            "lighting, tone - using the script model below, so the scene-breakdown pass has "
            "more to work with. Same story, just more vivid.")
        self.enhance_btn.clicked.connect(self._enhance_brief)
        erow.addWidget(self.enhance_btn)
        erow.addStretch(1)
        form.addRow("", erow)

        self.script_combo = ModelCombo(registry, settings, ("chat",), role="pipeline_script")
        form.addRow("Script model", self.script_combo)
        self.image_combo = ModelCombo(registry, settings, ("image_generate",), role="pipeline_image")
        form.addRow("Image model", self.image_combo)
        self.audio_combo = ModelCombo(registry, settings, ("tts",), role="pipeline_audio",
                                      allow_none=True, none_label="— None (silent, no narration) —")
        self.audio_combo.setToolTip(
            "Optional. When set, any scene with dialogue/voiceover text gets that line "
            "spoken and laid onto an audio track. Leave as None for a silent, visual-only "
            "movie (scenes still get a default on-screen duration).")
        form.addRow("Voice / TTS model", self.audio_combo)
        self.video_combo = ModelCombo(registry, settings, ("video_generate",), role="pipeline_video")
        form.addRow("Video model", self.video_combo)
        self.lipsync_combo = ModelCombo(registry, settings, ("lip_sync",), role="pipeline_lipsync",
                                        allow_none=True, none_label="— None (skip lip-sync) —")
        self.lipsync_combo.setToolTip(
            "Optional dedicated lip-sync pass, run right after each scene's video is "
            "generated - takes that video's own picture and hard-syncs the mouth to the "
            "scene's narration audio. Leave as None to skip it.")
        form.addRow("Lip-sync model", self.lipsync_combo)

        # Reference "cast" - uploaded once for the whole movie, merged into
        # every scene's own image-generation references alongside the
        # existing earlier-scenes-as-context chain (see
        # pipeline_orchestrator._image_context_refs). Mirrors generate_
        # panel.py's own reference-image row (references/add_reference/
        # _sync_refs/DropAcceptor) rather than inventing a new pattern.
        self.references: list[str] = []
        rrow = QHBoxLayout()
        self.ref_list = QLabel("none")
        self.ref_list.setWordWrap(True)
        add_ref = QPushButton("＋ file")
        add_ref.clicked.connect(self._add_ref_dialog)
        clr_ref = QPushButton("✕")
        clr_ref.setFixedWidth(30)
        clr_ref.clicked.connect(self._clear_refs)
        rrow.addWidget(self.ref_list, 1)
        rrow.addWidget(add_ref)
        rrow.addWidget(clr_ref)
        ref_row_label = label("Reference cast (optional)", dim=True)
        ref_row_label.setToolTip(
            "Character/style reference images used for every scene, on top of the automatic "
            "earlier-scene continuity references. Not every image model actually reads "
            "reference images (e.g. Google's Imagen ignores them) - if references don't seem "
            "to be having an effect, try a different image model.")
        form.addRow(ref_row_label, rrow)
        DropAcceptor(self, ("image",), lambda paths_: [self.add_reference(p) for p in paths_])

        self.target_minutes = QDoubleSpinBox()
        self.target_minutes.setRange(0.5, 180.0)
        self.target_minutes.setSingleStep(0.5)
        self.target_minutes.setValue(2.0)
        self.target_minutes.setSuffix(" min")
        self.target_minutes.setToolTip(
            "How long you want the finished movie to be. The AI still decides the real "
            "scene count from your brief during script breakdown - this and the default "
            "scene length below guide it and drive the cost preview, they don't hard-cap it.")
        form.addRow("Target length", self.target_minutes)

        self.default_seconds = QDoubleSpinBox()
        self.default_seconds.setDecimals(1)
        self.default_seconds.setToolTip(
            "Individual scenes are capped by the selected video model's own limit - a "
            "longer movie means more scenes, not longer ones.")
        form.addRow("Default scene length", self.default_seconds)
        self.video_combo.currentIndexChanged.connect(self._clamp_default_seconds)
        self._clamp_default_seconds()

        self.cost_label = label("", dim=True)
        self.cost_label.setWordWrap(True)
        form.addRow("", self.cost_label)
        for w in (self.target_minutes, self.default_seconds):
            w.valueChanged.connect(self._update_cost_estimate)
        for combo in (self.image_combo, self.video_combo, self.audio_combo):
            combo.currentIndexChanged.connect(self._update_cost_estimate)
        self._update_cost_estimate()

        buttons = QDialogButtonBox()
        self.go = buttons.addButton("🎬 Create", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self.pipeline: MoviePipeline | None = None

    def _enhance_brief(self):
        text = self.brief_edit.toPlainText().strip()
        if not text:
            return
        model = self.script_combo.current_model()
        if model is None:
            QMessageBox.information(self, "No model", "Pick a script model first.")
            return
        adapter = self.get_adapter(model.provider)
        sys_prompt = (
            "You are a screenwriter and cinematographer. Rewrite the user's movie/show "
            "brief into a more cinematic version of the SAME story: keep every plot beat, "
            "character and event, but add concrete visual detail - settings, character "
            "appearance, blocking, camera/lens choices, lighting, pacing, tone - so a later "
            "scene-breakdown pass has more to work with. Do not invent new plot or change "
            "the ending. Output ONLY the rewritten brief, no commentary, no headers.")

        def work(job):
            job.progress(-1, "Enhancing brief")
            return adapter.chat(model.id, [ChatMessage("user", text)], system=sys_prompt,
                                temperature=0.7)

        def done(result):
            rewritten = str(result).strip()
            if rewritten:
                self.brief_edit.setPlainText(rewritten)
            self.enhance_btn.setEnabled(True)
            self.enhance_btn.setText("✨ Enhance brief (AI)")

        def fail(msg):
            QMessageBox.warning(self, "Enhance failed", str(msg))
            self.enhance_btn.setEnabled(True)
            self.enhance_btn.setText("✨ Enhance brief (AI)")

        self.enhance_btn.setEnabled(False)
        self.enhance_btn.setText("Enhancing…")
        self.jobs.submit("Enhance movie brief", work, kind="chat", on_done=done, on_fail=fail)

    # ------------------------------------------------------------- references
    def add_reference(self, path: str):
        if path and Path(path).exists():
            self.references.append(path)
            self._sync_refs()

    def _add_ref_dialog(self):
        f, _ = QFileDialog.getOpenFileName(self, "Reference image", "", media_utils.IMAGE_FILTER)
        if f:
            self.add_reference(f)

    def _clear_refs(self):
        self.references = []
        self._sync_refs()

    def _sync_refs(self):
        self.ref_list.setText(", ".join(Path(r).name for r in self.references) or "none")

    def _clamp_default_seconds(self, *_args):
        """Keeps the default-scene-length spinbox honest about what the
        currently selected video model can actually do, rather than
        implying an arbitrary duration is achievable - see _seed_scene_
        durations in the orchestrator, which clamps the same way when
        seeding new scenes."""
        model = self.video_combo.current_model()
        spec = next((p for p in (model.params if model else [])
                    if p.get("name") == "duration"), None)
        if not spec:
            self.default_seconds.setEnabled(False)
            return
        self.default_seconds.setEnabled(True)
        if spec.get("type") == "choice":
            try:
                nums = [float(c) for c in (spec.get("choices") or [])]
            except (TypeError, ValueError):
                nums = []
            lo, hi = (min(nums), max(nums)) if nums else (1.0, 15.0)
        else:
            lo = float(spec.get("min", 1.0))
            hi = float(spec.get("max", 15.0))
        self.default_seconds.setRange(lo, hi)
        self.default_seconds.setSuffix(f" s (this model: {lo:g}-{hi:g}s)")
        try:
            default = float(spec.get("default", lo))
        except (TypeError, ValueError):
            default = lo
        self.default_seconds.setValue(max(lo, min(hi, default)))

    def _update_cost_estimate(self, *_args):
        seconds = max(1.0, self.default_seconds.value())
        n = max(1, math.ceil(self.target_minutes.value() * 60.0 / seconds))
        breakdown = []
        total = 0.0

        img = self.image_combo.current_model()
        if img:
            c = cost_estimator.estimate_cost(img, img.default_params(), count=n)
            if c is not None:
                breakdown.append(f"images ~${c:,.2f}")
                total += c

        vid = self.video_combo.current_model()
        if vid:
            c = cost_estimator.estimate_cost(vid, vid.default_params(), count=n)
            if c is not None:
                breakdown.append(f"video ~${c:,.2f}")
                total += c

        aud = self.audio_combo.current_model()
        if aud:
            # TTS is priced per character, driven by narration text that
            # doesn't exist until the script breakdown actually runs -
            # ~120 chars/scene (roughly one short spoken line) stands in
            # for a real prediction, just enough to give a ballpark.
            c = cost_estimator.estimate_cost(aud, aud.default_params(), count=n, text_len=120 * n)
            if c is not None:
                breakdown.append(f"narration ~${c:,.2f}")
                total += c

        if not breakdown:
            self.cost_label.setText(
                "Estimated cost: pricing unknown for the selected model(s).")
            return
        self.cost_label.setText(
            f"Estimated cost for {n} scenes: ~${total:,.2f} total ({', '.join(breakdown)}) - "
            "actual scene count and per-scene settings can change this.")

    def _accept(self):
        brief = self.brief_edit.toPlainText().strip()
        script = self.script_combo.current_model()
        image = self.image_combo.current_model()
        video = self.video_combo.current_model()
        if not brief:
            QMessageBox.information(self, "Brief needed", "Describe the movie/show first.")
            return
        if not (script and image and video):
            QMessageBox.information(
                self, "Model needed",
                "Pick a model for script, image and video - add API keys under "
                "AI ▸ API Keys… if a picker is empty.")
            return
        audio = self.audio_combo.current_model()
        lipsync = self.lipsync_combo.current_model()
        self.pipeline = MoviePipeline(
            name=self.name_edit.text().strip() or "Untitled movie", brief=brief,
            script_model=script.key, image_model=image.key,
            audio_model=audio.key if audio else "", video_model=video.key,
            lipsync_model=lipsync.key if lipsync else "",
            reference_images=list(self.references),
            default_scene_duration=self.default_seconds.value())
        self.accept()
