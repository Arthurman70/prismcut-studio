"""Keyboard shortcut cheat sheet - press ? anywhere in the app. Content is
generated from core.shortcuts.REGISTRY, the same list every real shortcut
binding registers itself into, so this can't go stale relative to what's
actually bound."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialogButtonBox, QHeaderView, QLineEdit, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from ...core import shortcuts


class ShortcutsDialog(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Dialog)
        self.setWindowTitle("Keyboard shortcuts")
        self.resize(480, 560)
        lay = QVBoxLayout(self)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter shortcuts…")
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Action", "Shortcut"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.tree, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        buttons.accepted.connect(self.close)
        buttons.clicked.connect(lambda *_: self.close())
        lay.addWidget(buttons)

        self._populate()
        self.search.setFocus()

    def _populate(self):
        self.tree.clear()
        for category, items in shortcuts.by_category().items():
            root = QTreeWidgetItem([category, ""])
            root.setFlags(root.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            font = root.font(0)
            font.setBold(True)
            root.setFont(0, font)
            self.tree.addTopLevelItem(root)
            for s in items:
                root.addChild(QTreeWidgetItem([s.action, s.keys]))
        self.tree.expandAll()

    def _filter(self, text: str):
        text = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(i)
            any_visible = False
            for j in range(root.childCount()):
                child = root.child(j)
                match = (not text or text in child.text(0).lower()
                        or text in child.text(1).lower())
                child.setHidden(not match)
                any_visible = any_visible or match
            root.setHidden(not any_visible)

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(ev)
