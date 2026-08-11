"""Model Manager - browse the registry, add/edit custom models (any provider,
any capability), hide models, import/export. Because model IDs change monthly,
this is data-driven: no code edits needed to track new releases."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from ...core.registry import CAPS, Registry
from ..widgets.common import label

PARAM_HELP = ('[{"name":"duration","label":"Duration (s)","type":"int","min":1,'
              '"max":15,"default":6}]  - types: int, float, choice, bool, text')


class ModelEditDialog(QDialog):
    def __init__(self, registry: Registry, model=None, parent=None):
        super().__init__(parent)
        self.registry = registry
        self.setWindowTitle("Edit model" if model else "Add custom model")
        self.resize(560, 480)
        form = QFormLayout(self)
        self.provider = QComboBox()
        for name, spec in registry.providers.items():
            self.provider.addItem(f"{spec.label} ({name})", name)
        self.model_id = QLineEdit()
        self.model_id.setPlaceholderText("exact API model id, e.g. veo-4.0-generate-preview")
        self.label_edit = QLineEdit()
        self.caps_boxes = {}
        caps_row = QHBoxLayout()
        col = QVBoxLayout()
        for i, cap in enumerate(CAPS):
            cb = QCheckBox(cap)
            self.caps_boxes[cap] = cb
            col.addWidget(cb)
            if i % 5 == 4:
                caps_row.addLayout(col)
                col = QVBoxLayout()
        caps_row.addLayout(col)
        self.params_edit = QPlainTextEdit()
        self.params_edit.setPlaceholderText(PARAM_HELP)
        self.notes = QLineEdit()
        form.addRow("Provider", self.provider)
        form.addRow("Model id", self.model_id)
        form.addRow("Label", self.label_edit)
        form.addRow("Capabilities", caps_row)
        form.addRow("Params (JSON)", self.params_edit)
        form.addRow("Notes", self.notes)
        if model:
            idx = self.provider.findData(model.provider)
            if idx >= 0:
                self.provider.setCurrentIndex(idx)
            self.model_id.setText(model.id)
            self.label_edit.setText(model.label)
            for cap in model.caps:
                if cap in self.caps_boxes:
                    self.caps_boxes[cap].setChecked(True)
            self.params_edit.setPlainText(json.dumps(model.params, indent=1) if model.params else "")
            self.notes.setText(model.notes)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _save(self):
        mid = self.model_id.text().strip()
        if not mid:
            QMessageBox.warning(self, "Model id", "Model id is required.")
            return
        params = []
        raw = self.params_edit.toPlainText().strip()
        if raw:
            try:
                params = json.loads(raw)
                assert isinstance(params, list)
            except Exception:
                QMessageBox.warning(self, "Params", "Params must be a JSON list of objects.\n"
                                    + PARAM_HELP)
                return
        self.registry.save_user_model({
            "id": mid, "provider": self.provider.currentData(),
            "label": self.label_edit.text().strip() or mid,
            "caps": [c for c, cb in self.caps_boxes.items() if cb.isChecked()],
            "params": params, "notes": self.notes.text().strip()})
        self.accept()


class ModelManagerDialog(QDialog):
    def __init__(self, registry: Registry, parent=None):
        super().__init__(parent)
        self.registry = registry
        self.setWindowTitle("Model Manager")
        self.resize(780, 560)
        lay = QVBoxLayout(self)
        lay.addWidget(label("The registry ships with sane defaults (verified Aug 2026) "
                            "but model IDs churn - add new ones here the day they launch. "
                            "Custom entries live in models.user.json.", dim=True))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Model", "Capabilities", "Notes"])
        self.tree.setColumnWidth(0, 330)
        self.tree.setColumnWidth(1, 200)
        lay.addWidget(self.tree, 1)
        row = QHBoxLayout()
        add = QPushButton("＋ Add model")
        add.clicked.connect(self._add)
        edit = QPushButton("✎ Edit (as custom)")
        edit.clicked.connect(self._edit)
        hide = QPushButton("👁 Hide/Show")
        hide.clicked.connect(self._toggle_hide)
        rm = QPushButton("🗑 Remove custom")
        rm.clicked.connect(self._remove)
        exp = QPushButton("Export JSON…")
        exp.clicked.connect(self._export)
        row.addWidget(add)
        row.addWidget(edit)
        row.addWidget(hide)
        row.addWidget(rm)
        row.addStretch(1)
        row.addWidget(exp)
        lay.addLayout(row)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        close.accepted.connect(self.accept)
        close.clicked.connect(lambda *_: self.accept())
        lay.addWidget(close)
        self.refresh()

    def refresh(self):
        self.tree.clear()
        by_provider: dict[str, QTreeWidgetItem] = {}
        for name, spec in self.registry.providers.items():
            node = QTreeWidgetItem([spec.label])
            node.setFlags(node.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            by_provider[name] = node
            self.tree.addTopLevelItem(node)
        for m in self.registry.models:
            hidden = m.key in self.registry.hidden
            flags = []
            if m.user:
                flags.append("custom")
            if hidden:
                flags.append("hidden")
            suffix = f"  [{', '.join(flags)}]" if flags else ""
            node = QTreeWidgetItem([f"{m.display}{suffix}", ", ".join(m.caps), m.notes[:80]])
            node.setData(0, Qt.ItemDataRole.UserRole, m.key)
            node.setToolTip(0, m.id)
            if hidden:
                node.setForeground(0, Qt.GlobalColor.darkGray)
            parent = by_provider.get(m.provider)
            if parent:
                parent.addChild(node)
        self.tree.expandAll()

    def _selected(self):
        it = self.tree.currentItem()
        key = it.data(0, Qt.ItemDataRole.UserRole) if it else None
        return self.registry.by_key(key) if key else None

    def _add(self):
        if ModelEditDialog(self.registry, parent=self).exec():
            self.refresh()

    def _edit(self):
        m = self._selected()
        if m and ModelEditDialog(self.registry, m, parent=self).exec():
            self.refresh()

    def _toggle_hide(self):
        m = self._selected()
        if m:
            self.registry.set_hidden(m.key, m.key not in self.registry.hidden)
            self.refresh()

    def _remove(self):
        m = self._selected()
        if m and m.user:
            self.registry.remove_user_model(m.provider, m.id)
            self.refresh()
        elif m:
            QMessageBox.information(self, "Built-in model",
                                    "Built-in models can be hidden, not removed. "
                                    "(Or edit as custom to override.)")

    def _export(self):
        f, _ = QFileDialog.getSaveFileName(self, "Export registry", "prismcut-models.json",
                                           "JSON (*.json)")
        if f:
            self.registry.export_json(f)
