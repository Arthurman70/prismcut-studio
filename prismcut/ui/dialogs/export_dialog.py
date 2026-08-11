"""Export / render dialog: resolution & format presets, ffmpeg render job."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QLineEdit, QPushButton, QSpinBox)

from ...core import media as media_utils
from ...core.project import Project
from ...core.render import RenderOptions, run_render
from ..widgets.common import SliderSpin, browse_row, label

PRESETS = [
    ("1080p · 1920×1080", 1920, 1080),
    ("4K UHD · 3840×2160", 3840, 2160),
    ("Vertical (Shorts/Reels) · 1080×1920", 1080, 1920),
    ("720p · 1280×720", 1280, 720),
    ("Square · 1080×1080", 1080, 1080),
]
FORMATS = [
    ("MP4 (H.264 + AAC)", "mp4", ".mp4"),
    ("MP4 (H.265/HEVC)", "mp4-hevc", ".mp4"),
    ("WebM (VP9 + Opus)", "webm", ".webm"),
    ("GIF (no audio)", "gif", ".gif"),
    ("PNG sequence", "png-seq", ".png"),
    ("Audio only · MP3", "mp3", ".mp3"),
    ("Audio only · WAV", "wav", ".wav"),
]


class ExportDialog(QDialog):
    def __init__(self, project: Project, jobs, parent=None):
        super().__init__(parent)
        self.project = project
        self.jobs = jobs
        self.setWindowTitle("Export timeline")
        self.resize(520, 340)
        form = QFormLayout(self)

        if not media_utils.have_ffmpeg():
            form.addRow(label("⚠️ ffmpeg was not found on PATH. Install it from "
                              "ffmpeg.org (winget install Gyan.FFmpeg) and restart.",
                              dim=False))

        self.preset = QComboBox()
        for name, _w, _h in PRESETS:
            self.preset.addItem(name)
        self.fmt = QComboBox()
        for name, _key, _ext in FORMATS:
            self.fmt.addItem(name)
        self.fmt.currentIndexChanged.connect(self._fmt_changed)
        self.fps = QSpinBox()
        self.fps.setRange(10, 60)
        self.fps.setValue(project.fps)
        self.quality = SliderSpin(14, 32, 20)
        self.out_edit = QLineEdit(str(Path.home() / "prismcut_export.mp4"))
        browse = QPushButton("…")
        browse.setFixedWidth(34)
        browse.clicked.connect(self._browse)

        form.addRow("Preset", self.preset)
        form.addRow("Format", self.fmt)
        form.addRow("FPS", self.fps)
        form.addRow("Quality (CRF, lower = better)", self.quality)
        form.addRow("Output file", browse_row(self.out_edit, browse))
        dur = project.duration()
        form.addRow(label(f"Timeline: {len(project.clips)} clip(s), {dur:.1f}s "
                          f"· {len(project.video_tracks())} video / "
                          f"{len(project.audio_tracks())} audio tracks", dim=True))

        buttons = QDialogButtonBox()
        self.go = buttons.addButton("🎬 Render", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._render)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _fmt_changed(self, idx: int):
        _name, _key, ext = FORMATS[idx]
        p = Path(self.out_edit.text() or "export")
        self.out_edit.setText(str(p.with_suffix(ext)))

    def _browse(self):
        _name, _key, ext = FORMATS[self.fmt.currentIndex()]
        f, _ = QFileDialog.getSaveFileName(self, "Output file", self.out_edit.text(),
                                           f"*{ext}")
        if f:
            self.out_edit.setText(f)

    def _render(self):
        _pn, w, h = PRESETS[self.preset.currentIndex()]
        _fn, key, ext = FORMATS[self.fmt.currentIndex()]
        out = Path(self.out_edit.text()).with_suffix(ext) if ext != ".png" else \
            Path(self.out_edit.text())
        opts = RenderOptions(width=w, height=h, fps=self.fps.value(), fmt=key,
                             crf=int(self.quality.value()), out_path=str(out))
        project = self.project

        def work(job):
            return run_render(project, opts, job)

        self.jobs.submit(f"Render → {out.name}", work, kind="render")
        self.accept()
