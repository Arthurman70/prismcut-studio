"""Export / render dialog: resolution & format presets, ffmpeg render job."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
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
    ("MOV (H.264 + AAC · QuickTime)", "mov", ".mov"),
    ("MOV (ProRes 422 · mastering/archival)", "mov-prores", ".mov"),
    ("MKV (H.264 + AAC)", "mkv", ".mkv"),
    ("WebM (VP9 + Opus)", "webm", ".webm"),
    ("GIF (no audio)", "gif", ".gif"),
    ("PNG sequence", "png-seq", ".png"),
    ("Audio only · MP3", "mp3", ".mp3"),
    ("Audio only · WAV (uncompressed)", "wav", ".wav"),
    ("Audio only · FLAC (lossless)", "flac", ".flac"),
    ("Audio only · M4A/AAC", "m4a", ".m4a"),
]
NVENC_CAPABLE_FORMATS = {"mp4", "mov", "mkv", "mp4-hevc"}


class ExportDialog(QDialog):
    def __init__(self, project: Project, jobs, settings, parent=None):
        super().__init__(parent)
        self.project = project
        self.jobs = jobs
        self.settings = settings
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
        self.preset.setCurrentIndex(min(int(settings.get("export/preset", 0) or 0),
                                        len(PRESETS) - 1))
        self.fmt = QComboBox()
        for name, _key, _ext in FORMATS:
            self.fmt.addItem(name)
        self.fmt.setCurrentIndex(min(int(settings.get("export/format", 0) or 0),
                                     len(FORMATS) - 1))
        self.fps = QSpinBox()
        self.fps.setRange(10, 60)
        self.fps.setValue(project.fps)
        self.quality = SliderSpin(14, 32, 20)
        self.nvenc_check = QCheckBox("Hardware encoding (NVENC) - faster, needs an NVIDIA GPU")
        self.nvenc_check.setChecked(settings.get_bool("export/use_nvenc", False))
        default_dir = str(settings.get("export/last_dir", "") or Path.home())
        _fn, _key, default_ext = FORMATS[self.fmt.currentIndex()]
        self.out_edit = QLineEdit(str(Path(default_dir) / f"prismcut_export{default_ext}"))
        browse = QPushButton("…")
        browse.setFixedWidth(34)
        browse.clicked.connect(self._browse)
        self._fmt_changed(self.fmt.currentIndex())   # sync nvenc_check's enabled state up front
        self.fmt.currentIndexChanged.connect(self._fmt_changed)

        form.addRow("Preset", self.preset)
        form.addRow("Format", self.fmt)
        form.addRow("FPS", self.fps)
        form.addRow("Quality (CRF, lower = better)", self.quality)
        form.addRow(self.nvenc_check)
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
        _name, key, ext = FORMATS[idx]
        nvenc_ok = key in NVENC_CAPABLE_FORMATS
        self.nvenc_check.setEnabled(nvenc_ok)
        if not nvenc_ok:
            self.nvenc_check.setChecked(False)
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
                             crf=int(self.quality.value()), use_nvenc=self.nvenc_check.isChecked(),
                             out_path=str(out))
        project = self.project
        # This dialog closes itself right after submitting (below), so
        # feedback once the render actually finishes has to reach back to
        # the main window - same self.parent() pattern update_dialog.py
        # already uses to reach MainWindow from a modal dialog.
        win = self.parent()

        def work(job):
            return run_render(project, opts, job)

        def done(_result):
            if win is not None:
                win.toast(f"Exported to {out}", "success")

        def fail(msg):
            if win is not None:
                win.toast(f"Export failed: {msg}", "error", 8000)

        self.jobs.submit(f"Render → {out.name}", work, kind="render",
                         on_done=done, on_fail=fail)
        self.settings.set("export/preset", self.preset.currentIndex())
        self.settings.set("export/format", self.fmt.currentIndex())
        self.settings.set("export/use_nvenc", self.nvenc_check.isChecked())
        self.settings.set("export/last_dir", str(out.parent))
        self.accept()
