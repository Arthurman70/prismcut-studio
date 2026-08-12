"""New movie dialog: one brief + one model picker per pipeline stage."""
from __future__ import annotations

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLineEdit,
                               QMessageBox, QPlainTextEdit)

from ...core.pipeline import MoviePipeline
from ..widgets.common import ModelCombo


class NewPipelineDialog(QDialog):
    def __init__(self, registry, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New movie")
        self.resize(560, 460)
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

        self.script_combo = ModelCombo(registry, settings, ("chat",), role="pipeline_script")
        form.addRow("Script model", self.script_combo)
        self.image_combo = ModelCombo(registry, settings, ("image_generate",), role="pipeline_image")
        form.addRow("Image model", self.image_combo)
        self.audio_combo = ModelCombo(registry, settings, ("tts",), role="pipeline_audio")
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

        buttons = QDialogButtonBox()
        self.go = buttons.addButton("🎬 Create", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self.pipeline: MoviePipeline | None = None

    def _accept(self):
        brief = self.brief_edit.toPlainText().strip()
        script = self.script_combo.current_model()
        image = self.image_combo.current_model()
        audio = self.audio_combo.current_model()
        video = self.video_combo.current_model()
        if not brief:
            QMessageBox.information(self, "Brief needed", "Describe the movie/show first.")
            return
        if not (script and image and audio and video):
            QMessageBox.information(
                self, "Model needed",
                "Pick a model for script, image, voice and video - add API keys under "
                "AI ▸ API Keys… if a picker is empty.")
            return
        lipsync = self.lipsync_combo.current_model()
        self.pipeline = MoviePipeline(
            name=self.name_edit.text().strip() or "Untitled movie", brief=brief,
            script_model=script.key, image_model=image.key, audio_model=audio.key,
            video_model=video.key, lipsync_model=lipsync.key if lipsync else "")
        self.accept()
