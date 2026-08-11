"""Nano Tools - a grid of prefilled one-click AI edit tools (Nano Banana & co).

Each button fires its prompt (with the strength wording substituted) at the
currently selected image-edit model. Tools live in assets/nano_tools.json and
can be extended by the user (custom tools are stored in the app data dir).
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QGridLayout, QInputDialog, QLabel, QPlainTextEdit,
                               QPushButton, QScrollArea, QSlider, QTabWidget,
                               QVBoxLayout, QWidget)

from ...core import paths
from ..widgets.common import ModelCombo, accent_button, label

TOOLS_PATH = Path(__file__).resolve().parent.parent.parent / "assets" / "nano_tools.json"


def _user_tools_path() -> Path:
    return paths.app_data_dir() / "nano_tools.user.json"


class NanoToolsPanel(QWidget):
    runRequested = Signal(str, bool, str)   # prompt, needs_mask, tool_name

    def __init__(self, registry, settings, parent=None):
        super().__init__(parent)
        self.registry = registry
        self.settings = settings
        data = json.loads(TOOLS_PATH.read_text(encoding="utf-8"))
        self.strength_words: dict = data.get("strength_words", {})
        self.tools: list[dict] = list(data.get("tools", []))
        up = _user_tools_path()
        if up.exists():
            try:
                self.tools += json.loads(up.read_text(encoding="utf-8")).get("tools", [])
            except Exception:
                pass

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        lay.addWidget(label("AI edit model", dim=True))
        self.model_combo = ModelCombo(registry, settings, ("image_edit",), role="edit_model")
        lay.addWidget(self.model_combo)

        srow = QWidget()
        sl = QVBoxLayout(srow)
        sl.setContentsMargins(0, 0, 0, 0)
        self.strength_label = QLabel("Strength: moderately")
        self.strength_label.setProperty("dim", "true")
        self.strength = QSlider(Qt.Orientation.Horizontal)
        self.strength.setRange(1, 5)
        self.strength.setValue(3)
        self.strength.valueChanged.connect(self._strength_changed)
        sl.addWidget(self.strength_label)
        sl.addWidget(self.strength)
        lay.addWidget(srow)

        self.tabs = QTabWidget()
        cats: dict[str, list[dict]] = {}
        for t in self.tools:
            cats.setdefault(t.get("category", "Other"), []).append(t)
        for cat, tools in cats.items():
            page = QWidget()
            grid = QGridLayout(page)
            grid.setContentsMargins(4, 6, 4, 6)
            grid.setSpacing(5)
            for i, tool in enumerate(tools):
                btn = QPushButton(f"{tool.get('emoji', '✨')}  {tool['name']}")
                btn.setToolTip(tool["prompt"])
                btn.setStyleSheet("text-align:left;padding:7px 9px;")
                btn.clicked.connect(lambda _=False, tl=tool: self._fire(tl))
                grid.addWidget(btn, i // 2, i % 2)
            grid.setRowStretch(len(tools) // 2 + 1, 1)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            self.tabs.addTab(scroll, cat)
        lay.addWidget(self.tabs, 1)

        lay.addWidget(label("Custom edit prompt", dim=True))
        self.custom = QPlainTextEdit()
        self.custom.setPlaceholderText("Describe any edit… e.g. “make it look like "
                                       "a rainy night, reflections on the street”")
        self.custom.setFixedHeight(64)
        lay.addWidget(self.custom)
        run = accent_button("✨ Run custom edit")
        run.clicked.connect(self._fire_custom)
        lay.addWidget(run)
        add = QPushButton("＋ Save custom prompt as tool")
        add.clicked.connect(self._save_custom_tool)
        lay.addWidget(add)

    # ------------------------------------------------------------------
    def _strength_changed(self, v: int):
        self.strength_label.setText(f"Strength: {self.strength_words.get(str(v), 'moderately')}")

    def _word(self) -> str:
        return self.strength_words.get(str(self.strength.value()), "moderately")

    def _fire(self, tool: dict):
        prompt = tool["prompt"].replace("{strength}", self._word())
        self.runRequested.emit(prompt, bool(tool.get("needs_mask")), tool["name"])

    def _fire_custom(self):
        text = self.custom.toPlainText().strip()
        if text:
            self.runRequested.emit(text, False, "Custom edit")

    def _save_custom_tool(self):
        text = self.custom.toPlainText().strip()
        if not text:
            return
        name, ok = QInputDialog.getText(self, "Save tool", "Tool name:")
        if not ok or not name.strip():
            return
        up = _user_tools_path()
        data = {"tools": []}
        if up.exists():
            try:
                data = json.loads(up.read_text(encoding="utf-8"))
            except Exception:
                pass
        data.setdefault("tools", []).append({
            "id": name.lower().replace(" ", "_")[:24], "name": name.strip(),
            "emoji": "⭐", "category": "My tools", "prompt": text})
        up.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def current_model(self):
        return self.model_combo.current_model()
