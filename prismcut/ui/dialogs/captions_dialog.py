"""Minimal captions review/edit dialog - shows generated segments as
editable SRT text and saves wherever the user picks. "Sidecar" just means
wherever makes sense for their export; nothing here ties the save location
to a specific video file, so it works equally well saved next to the
source media or next to an already-exported video."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QLabel,
                               QPlainTextEdit, QVBoxLayout)

from ...core.captions import Segment, format_srt


class CaptionsDialog(QDialog):
    def __init__(self, segments: list[Segment], default_path, parent=None):
        super().__init__(parent)
        self.default_path = Path(default_path)
        self.setWindowTitle("Generated captions")
        self.resize(560, 420)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"{len(segments)} caption(s) — review or edit the text below, "
                             "then save as .srt. Timestamps aren't editable in this view."))
        self.text_edit = QPlainTextEdit(format_srt(segments))
        lay.addWidget(self.text_edit, 1)

        buttons = QDialogButtonBox()
        self.go = buttons.addButton("💾 Save .srt…", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _save(self):
        f, _ = QFileDialog.getSaveFileName(self, "Save captions", str(self.default_path), "*.srt")
        if not f:
            return
        Path(f).write_text(self.text_edit.toPlainText(), encoding="utf-8")
        self.accept()
