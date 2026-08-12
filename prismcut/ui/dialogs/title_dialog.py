"""Minimal title/text clip dialog: text, size, color, position, bold, and
drop-shadow - creates a synthetic Project.add_title() media item. Placing
it on the timeline is the caller's job (project_bin.py), the same way
importing a file only adds it to the bin rather than also placing a clip."""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QLineEdit, QSpinBox)

from ...core.project import MediaItem, Project

TITLE_COLORS = [
    ("White", "#ffffff"), ("Black", "#000000"), ("Amber", "#e8a33d"),
    ("Red", "#ef5350"), ("Blue", "#42a5f5"),
]
POSITIONS = [("Center", "center"), ("Top", "top"), ("Bottom", "bottom")]


class TitleDialog(QDialog):
    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("Add title")
        self.resize(420, 280)
        form = QFormLayout(self)

        self.text_edit = QLineEdit()
        self.text_edit.setPlaceholderText("Title text")
        self.size_spin = QSpinBox()
        self.size_spin.setRange(12, 400)
        self.size_spin.setValue(64)
        self.color_combo = QComboBox()
        for name, _hex in TITLE_COLORS:
            self.color_combo.addItem(name)
        self.position_combo = QComboBox()
        for name, _key in POSITIONS:
            self.position_combo.addItem(name)
        self.bold_check = QCheckBox("Bold")
        self.bold_check.setChecked(True)
        self.shadow_check = QCheckBox("Drop shadow")
        self.shadow_check.setChecked(True)
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(1, 120)
        self.duration_spin.setValue(5)
        self.duration_spin.setSuffix(" s")

        form.addRow("Text", self.text_edit)
        form.addRow("Font size", self.size_spin)
        form.addRow("Color", self.color_combo)
        form.addRow("Position", self.position_combo)
        form.addRow(self.bold_check)
        form.addRow(self.shadow_check)
        form.addRow("Duration", self.duration_spin)

        buttons = QDialogButtonBox()
        self.go = buttons.addButton("➕ Add Title", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.text_edit.setFocus()

    def result_media(self) -> MediaItem:
        """Call after exec() returns Accepted - creates (and returns) the
        title MediaItem in the project. Doesn't place it on the timeline."""
        text = self.text_edit.text().strip() or "Title"
        color = TITLE_COLORS[self.color_combo.currentIndex()][1]
        position = POSITIONS[self.position_combo.currentIndex()][1]
        return self.project.add_title(
            text, font_size=self.size_spin.value(), color=color, position=position,
            bold=self.bold_check.isChecked(), shadow=self.shadow_check.isChecked(),
            duration=float(self.duration_spin.value()))
