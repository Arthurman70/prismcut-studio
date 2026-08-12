"""AI Chat - talk to any connected model (Gemini, GPT, Claude, Grok, DeepSeek,
MiniMax, custom endpoints…) with streaming, attachments and saved conversations.

The model toggle at the top is populated from the registry and marks providers
whose API key is present with 🔑.
"""
from __future__ import annotations

import html
import json
import time
import uuid
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
                               QInputDialog, QLabel, QMessageBox, QPlainTextEdit,
                               QPushButton, QSlider, QTextBrowser, QVBoxLayout, QWidget)

from ...core import media as media_utils
from ...core import paths
from ...providers.base import ChatMessage
from .. import theme
from ..widgets.common import DropAcceptor, ModelCombo, accent_button


class ChatWorker(QThread):
    delta = Signal(str)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, adapter, model_id: str, messages: list[ChatMessage],
                 system: str, temperature: float, tools=None, on_tool_call=None):
        super().__init__()
        self.adapter = adapter
        self.model_id = model_id
        self.messages = messages
        self.system = system
        self.temperature = temperature
        self.tools = tools
        self.on_tool_call = on_tool_call
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            text = self.adapter.chat(self.model_id, self.messages, system=self.system,
                                     temperature=self.temperature,
                                     on_delta=self.delta.emit,
                                     should_cancel=lambda: self._stop,
                                     tools=self.tools, on_tool_call=self.on_tool_call)
            self.done.emit(text)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


def _md_to_html(text: str) -> str:
    """Tiny safe markdown-ish renderer (code blocks, inline code, bold, italics)."""
    esc = html.escape(text)
    out, in_code = [], False
    for line in esc.split("\n"):
        if line.strip().startswith("```"):
            out.append("</pre>" if in_code else
                       f"<pre style='background:{theme.PANEL_ALT};padding:6px;border-radius:6px;'>")
            in_code = not in_code
            continue
        if not in_code:
            import re
            line = re.sub(r"`([^`]+)`",
                          rf"<code style='background:{theme.PANEL_ALT};padding:1px 4px;"
                          r"border-radius:3px;'>\1</code>",
                          line)
            line = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", line)
            line = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", line)
            out.append(line + "<br>")
        else:
            out.append(line + "\n")
    if in_code:
        out.append("</pre>")
    return "".join(out)


class ChatPanel(QWidget):
    status = Signal(str)

    def __init__(self, registry, settings, get_adapter, agent=None, parent=None):
        super().__init__(parent)
        self.registry = registry
        self.settings = settings
        self.get_adapter = get_adapter
        self.agent = agent
        self.messages: list[ChatMessage] = []
        self.pending_attachments: list[str] = []
        self.system_prompt = str(settings.get("chat/system", ""))
        self.worker: ChatWorker | None = None
        self.chat_id = uuid.uuid4().hex[:8]
        self._stream_buf = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(5)

        top = QHBoxLayout()
        self.model_combo = ModelCombo(registry, settings, ("chat",), role="chat")
        top.addWidget(self.model_combo, 1)
        sys_btn = QPushButton("⚙")
        sys_btn.setFixedWidth(30)
        sys_btn.setToolTip("System prompt")
        sys_btn.clicked.connect(self._edit_system)
        top.addWidget(sys_btn)
        lay.addLayout(top)

        if self.agent is not None:
            arow = QHBoxLayout()
            self.agent_mode = QCheckBox("🤖 Agent Mode - let the AI take actions in the app")
            self.agent_mode.setChecked(settings.get_bool("chat/agent_mode", False))
            self.agent_mode.setToolTip(
                "When on, the AI can call tools to add/adjust clips, queue generations, etc. "
                "Every change it makes still goes through the same confirm dialog and is undoable.")
            self.agent_mode.toggled.connect(lambda on: settings.set("chat/agent_mode", on))
            arow.addWidget(self.agent_mode, 1)
            lay.addLayout(arow)
        else:
            self.agent_mode = None

        trow = QHBoxLayout()
        trow.addWidget(QLabel("Temp"))
        self.temp = QSlider(Qt.Orientation.Horizontal)
        self.temp.setRange(0, 20)
        self.temp.setValue(int(float(settings.get("chat/temperature", 0.7)) * 10))
        trow.addWidget(self.temp, 1)
        new_btn = QPushButton("🆕")
        new_btn.setToolTip("New conversation")
        new_btn.setFixedWidth(32)
        new_btn.setAccessibleName("New conversation")
        new_btn.clicked.connect(self.new_chat)
        save_btn = QPushButton("💾")
        save_btn.setToolTip("Save conversation")
        save_btn.setFixedWidth(32)
        save_btn.setAccessibleName("Save conversation")
        save_btn.clicked.connect(self.save_chat)
        load_btn = QPushButton("📂")
        load_btn.setToolTip("Load conversation")
        load_btn.setFixedWidth(32)
        load_btn.setAccessibleName("Load conversation")
        load_btn.clicked.connect(self.load_chat)
        copy_btn = QPushButton("⧉")
        copy_btn.setToolTip("Copy last response")
        copy_btn.setFixedWidth(32)
        copy_btn.setAccessibleName("Copy last response")
        copy_btn.clicked.connect(self._copy_last_response)
        trow.addWidget(new_btn)
        trow.addWidget(save_btn)
        trow.addWidget(load_btn)
        trow.addWidget(copy_btn)
        lay.addLayout(trow)

        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.view.setObjectName("chatView")
        lay.addWidget(self.view, 1)

        self.attach_label = QLabel("")
        self.attach_label.setProperty("dim", "true")
        self.attach_label.setWordWrap(True)
        lay.addWidget(self.attach_label)

        arow = QHBoxLayout()
        attach = QPushButton("📎 Attach")
        attach.setToolTip("Attach image / audio / video - files are uploaded to the "
                          "selected provider with the right encoding")
        attach.clicked.connect(self._attach_dialog)
        clear_a = QPushButton("✕")
        clear_a.setFixedWidth(28)
        clear_a.setToolTip("Clear attachments")
        clear_a.clicked.connect(self._clear_attachments)
        arow.addWidget(attach)
        arow.addWidget(clear_a)
        arow.addStretch(1)
        lay.addLayout(arow)

        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("Message the AI…   (Ctrl+Enter to send)")
        self.input.setFixedHeight(72)
        self.input.installEventFilter(self)
        lay.addWidget(self.input)

        # Form draft auto-recovery: debounced so we're not hitting QSettings
        # on every keystroke, cleared once the message actually sends.
        self._draft_timer = QTimer(self)
        self._draft_timer.setSingleShot(True)
        self._draft_timer.setInterval(800)
        self._draft_timer.timeout.connect(
            lambda: self.settings.set("chat/draft", self.input.toPlainText()))
        self.input.textChanged.connect(self._draft_timer.start)
        draft = str(self.settings.get("chat/draft", ""))
        if draft:
            self.input.setPlainText(draft)

        srow = QHBoxLayout()
        self.send_btn = accent_button("Send ➤")
        self.send_btn.clicked.connect(self.send)
        self.stop_btn = QPushButton("⏹ Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_worker)
        srow.addWidget(self.send_btn, 1)
        srow.addWidget(self.stop_btn)
        lay.addLayout(srow)
        self._render()

        DropAcceptor(self, ("image", "video", "audio"),
                    lambda paths_: [self.attach_file(p) for p in paths_],
                    on_rejected=lambda bad: self.status.emit(
                        f"Skipped {len(bad)} unsupported attachment(s)."))

    # ------------------------------------------------------------------ utils
    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent
        if obj is self.input and ev.type() == QEvent.Type.KeyPress:
            kev: QKeyEvent = ev
            if kev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and \
                    kev.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.send()
                return True
        return super().eventFilter(obj, ev)

    def _edit_system(self):
        text, ok = QInputDialog.getMultiLineText(self, "System prompt",
                                                 "Instructions for the AI:",
                                                 self.system_prompt)
        if ok:
            self.system_prompt = text
            self.settings.set("chat/system", text)

    def attach_file(self, path: str):
        if path and Path(path).exists():
            self.pending_attachments.append(path)
            self.attach_label.setText("📎 " + ", ".join(Path(p).name
                                                        for p in self.pending_attachments))

    def _attach_dialog(self):
        f, _ = QFileDialog.getOpenFileName(self, "Attach media", "", media_utils.MEDIA_FILTER)
        if f:
            self.attach_file(f)

    def _clear_attachments(self):
        self.pending_attachments = []
        self.attach_label.setText("")

    def _copy_last_response(self):
        last = next((m for m in reversed(self.messages) if m.role == "assistant"), None)
        if last is None:
            self.status.emit("No AI response to copy yet.")
            return
        QApplication.clipboard().setText(last.text)
        self.status.emit("✓ Copied to clipboard")

    # ------------------------------------------------------------------ render
    def _render(self, streaming: str = ""):
        parts = ["<style>p{margin:0 0 6px 0;}</style>"]
        if not self.messages and not streaming:
            parts.append(f"<p style='color:{theme.TEXT_DIM};'>Pick a model above and say hi. "
                         "Attach images/audio/video with 📎 - PrismCut encodes files "
                         "correctly for each provider.</p>")
        for m in self.messages:
            who = "You" if m.role == "user" else "AI"
            color = theme.ORANGE if m.role == "user" else theme.ACCENT
            att = ""
            if m.attachments:
                att = (f"<div style='color:{theme.TEXT_DIM};font-size:11px;'>📎 "
                       + ", ".join(html.escape(Path(a).name) for a in m.attachments) + "</div>")
            parts.append(f"<div style='margin:7px 0;'><b style='color:{color};'>{who}</b>"
                         f"{att}<div>{_md_to_html(m.text)}</div></div>")
        if streaming:
            parts.append(f"<div style='margin:7px 0;'><b style='color:{theme.ACCENT};'>AI</b>"
                         f"<div>{_md_to_html(streaming)}▌</div></div>")
        self.view.setHtml("".join(parts))
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    def append_message(self, role: str, text: str, attachments: list | None = None):
        self.messages.append(ChatMessage(role, text, attachments or []))
        self._render()

    # ------------------------------------------------------------------ send
    def send(self):
        if self.worker is not None:
            return
        text = self.input.toPlainText().strip()
        if not text and not self.pending_attachments:
            return
        model = self.model_combo.current_model()
        if model is None:
            QMessageBox.information(self, "No model",
                                    "No chat model available - add an API key under "
                                    "AI ▸ API Keys…")
            return
        try:
            adapter = self.get_adapter(model.provider)
            adapter.key()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "API key", str(exc))
            return
        self.messages.append(ChatMessage("user", text, list(self.pending_attachments)))
        self.input.clear()
        self.settings.set("chat/draft", "")
        self._clear_attachments()
        self._render()
        self._stream_buf = ""
        use_agent = bool(self.agent_mode and self.agent_mode.isChecked() and self.agent)
        self.worker = ChatWorker(adapter, model.id, list(self.messages),
                                 self.system_prompt, self.temp.value() / 10.0,
                                 tools=self.agent.tools if use_agent else None,
                                 on_tool_call=self._on_tool_call if use_agent else None)
        self.worker.delta.connect(self._on_delta)
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self._worker_finished)
        self.send_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.worker.start()

    def _on_tool_call(self, call):
        """Runs on the ChatWorker (background) thread, inside the adapter's
        tool loop. self.agent.dispatch() does its own hop to the GUI thread
        internally (confirm dialog + undo push need to happen there) and
        blocks until that's done, so by the time this returns the action
        has actually happened (or been declined)."""
        result = self.agent.dispatch(call)
        prefix = "⚠" if result.is_error else "🔧"
        self.status.emit(f"{prefix} {call.name}: {result.content[:120]}")
        return result

    def _on_delta(self, chunk: str):
        self._stream_buf += chunk
        self._render(streaming=self._stream_buf)

    def _on_done(self, text: str):
        self.messages.append(ChatMessage("assistant", text or self._stream_buf))
        self._render()

    def _on_failed(self, msg: str):
        self.messages.append(ChatMessage("assistant", f"⚠️ **Error:** {msg}"))
        self._render()
        self.status.emit(f"Chat error: {msg}")

    def _worker_finished(self):
        self.worker = None
        self.send_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.settings.set("chat/temperature", self.temp.value() / 10.0)

    def _stop_worker(self):
        if self.worker:
            self.worker.stop()

    # ------------------------------------------------------------------ persist
    def new_chat(self):
        self.messages = []
        self.chat_id = uuid.uuid4().hex[:8]
        self._render()

    def save_chat(self):
        if not self.messages:
            return
        name = time.strftime("%Y%m%d_%H%M") + f"_{self.chat_id}.json"
        p = paths.chats_dir() / name
        p.write_text(json.dumps({
            "system": self.system_prompt,
            "messages": [{"role": m.role, "text": m.text, "attachments": m.attachments}
                         for m in self.messages]}, indent=1), encoding="utf-8")
        self.status.emit(f"Conversation saved ({name})")

    def load_chat(self):
        files = sorted(paths.chats_dir().glob("*.json"), reverse=True)
        if not files:
            self.status.emit("No saved conversations yet.")
            return
        names = [f.name for f in files]
        name, ok = QInputDialog.getItem(self, "Load conversation", "Conversation:",
                                        names, 0, False)
        if not ok:
            return
        data = json.loads((paths.chats_dir() / name).read_text(encoding="utf-8"))
        self.system_prompt = data.get("system", "")
        self.messages = [ChatMessage(m["role"], m["text"], m.get("attachments", []))
                         for m in data.get("messages", [])]
        self._render()
