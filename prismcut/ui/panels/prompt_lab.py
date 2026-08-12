"""Prompt Lab: templates with {variables}, style-tuning token groups, live
preview, history - then push to the Generate panel."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QPushButton, QScrollArea, QTabWidget,
                               QVBoxLayout, QWidget)

from ...core import paths
from ..widgets.common import accent_button, label

TPL_PATH = Path(__file__).resolve().parent.parent.parent / "assets" / "prompt_templates.json"
VAR_RE = re.compile(r"\{([a-zA-Z0-9_ ]+)\}")


class PromptLab(QWidget):
    sendToGenerate = Signal(str, str)     # prompt, kind (image/video/music)
    status = Signal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        data = json.loads(TPL_PATH.read_text(encoding="utf-8"))
        self.templates: list[dict] = data.get("templates", [])
        self.tuning: dict[str, list[str]] = data.get("tuning", {})

        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        lay.addWidget(label("Template", dim=True))
        self.tpl_combo = QComboBox()
        self.tpl_combo.addItem("— free form —")
        for t in self.templates:
            self.tpl_combo.addItem(f"{t['name']}  ({t.get('kind', 'image')})")
        self.tpl_combo.currentIndexChanged.connect(self._tpl_changed)
        lay.addWidget(self.tpl_combo)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("Write a prompt or pick a template. "
                                       "{placeholders} become fields below.")
        self.editor.setMinimumHeight(70)
        self.editor.textChanged.connect(self._rebuild_vars)
        lay.addWidget(self.editor)

        self._draft_timer = QTimer(self)
        self._draft_timer.setSingleShot(True)
        self._draft_timer.setInterval(800)
        self._draft_timer.timeout.connect(
            lambda: self.settings.set("promptlab/draft", self.editor.toPlainText()))
        self.editor.textChanged.connect(self._draft_timer.start)

        self.var_host = QWidget()
        self.var_form = QFormLayout(self.var_host)
        self.var_form.setContentsMargins(0, 0, 0, 0)
        self.var_edits: dict[str, QLineEdit] = {}
        lay.addWidget(self.var_host)

        lay.addWidget(label("Style tuning (click to toggle tokens)", dim=True))
        self.tuning_tabs = QTabWidget()
        self.token_buttons: list[QPushButton] = []
        for group, tokens in self.tuning.items():
            page = QWidget()
            pv = QVBoxLayout(page)
            pv.setContentsMargins(4, 4, 4, 4)
            for tok in tokens:
                b = QPushButton(tok)
                b.setCheckable(True)
                b.setStyleSheet("text-align:left;padding:4px 8px;")
                b.toggled.connect(self._update_preview)
                pv.addWidget(b)
                self.token_buttons.append(b)
            pv.addStretch(1)
            sc = QScrollArea()
            sc.setWidget(page)
            sc.setWidgetResizable(True)
            sc.setFrameShape(QScrollArea.Shape.NoFrame)
            self.tuning_tabs.addTab(sc, group)
        self.tuning_tabs.setFixedHeight(190)
        lay.addWidget(self.tuning_tabs)

        lay.addWidget(label("Final prompt", dim=True))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(80)
        self.preview.setStyleSheet("color:#9be7ff;")
        lay.addWidget(self.preview)

        row = QHBoxLayout()
        to_img = accent_button("→ Image gen")
        to_img.clicked.connect(lambda: self._send("image"))
        to_vid = QPushButton("→ Video gen")
        to_vid.clicked.connect(lambda: self._send("video"))
        to_music = QPushButton("→ Music")
        to_music.clicked.connect(lambda: self._send("music"))
        row.addWidget(to_img)
        row.addWidget(to_vid)
        row.addWidget(to_music)
        lay.addLayout(row)

        lay.addWidget(label("History (from Generate panel)", dim=True))
        self.history = QListWidget()
        self.history.setFixedHeight(140)
        self.history.itemDoubleClicked.connect(self._reuse_history)
        lay.addWidget(self.history)
        refresh = QPushButton("↻ Refresh history")
        refresh.clicked.connect(self.load_history)
        lay.addWidget(refresh)
        lay.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidget(host)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)
        self.load_history()
        draft = str(self.settings.get("promptlab/draft", ""))
        if draft:
            self.editor.setPlainText(draft)   # triggers _rebuild_vars/_update_preview itself
        else:
            self._update_preview()

    # ------------------------------------------------------------------
    def _tpl_changed(self, idx: int):
        if idx > 0:
            self.editor.setPlainText(self.templates[idx - 1]["text"])

    def _rebuild_vars(self):
        wanted = VAR_RE.findall(self.editor.toPlainText())
        current = dict(self.var_edits)
        while self.var_form.rowCount():
            self.var_form.removeRow(0)
        self.var_edits = {}
        for name in dict.fromkeys(wanted):
            edit = QLineEdit(current[name].text() if name in current else "")
            edit.setPlaceholderText(name)
            edit.textChanged.connect(self._update_preview)
            self.var_edits[name] = edit
            self.var_form.addRow(name, edit)
        self._update_preview()

    def final_prompt(self) -> str:
        text = self.editor.toPlainText()
        for name, edit in self.var_edits.items():
            if edit.text().strip():
                text = text.replace("{" + name + "}", edit.text().strip())
        tokens = [b.text() for b in self.token_buttons if b.isChecked()]
        if tokens:
            text = (text.rstrip().rstrip(",") + ", " + ", ".join(tokens)) if text.strip() else \
                ", ".join(tokens)
        return text.strip()

    def _update_preview(self, *_a):
        self.preview.setPlainText(self.final_prompt())

    def _send(self, kind: str):
        prompt = self.final_prompt()
        if prompt:
            self.sendToGenerate.emit(prompt, kind)
            self.status.emit(f"Prompt sent to {kind} generation")
            # Not clearing the draft here: unlike Chat, sending to Generate
            # doesn't consume the prompt (it's still useful to keep editing/
            # re-send), so there's nothing stale to clear.

    # ------------------------------------------------------------------
    def load_history(self):
        self.history.clear()
        p = paths.prompt_history_path()
        if not p.exists():
            return
        try:
            hist = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return
        for h in reversed(hist[-100:]):
            ts = time.strftime("%m-%d %H:%M", time.localtime(h.get("ts", 0)))
            it = QListWidgetItem(f"[{ts} · {h.get('mode', '?')}] {h.get('prompt', '')[:80]}")
            it.setData(Qt.ItemDataRole.UserRole, h.get("prompt", ""))
            it.setToolTip(h.get("prompt", ""))
            self.history.addItem(it)

    def _reuse_history(self, item):
        self.editor.setPlainText(item.data(Qt.ItemDataRole.UserRole))
