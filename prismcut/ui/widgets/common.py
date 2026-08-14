"""Small reusable widgets."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QSlider,
                               QSpinBox, QToolButton, QVBoxLayout, QWidget)

from ...core import cost_estimator
from ...core import media as media_utils
from ...core import spellcheck
from ...core.registry import ModelSpec, Registry

# Shared job/scene status glyphs - single copy so JobsPanel and the movie
# pipeline's per-scene rows render the same icon for the same state.
STATUS_ICONS = {"queued": "⏳", "running": "⚙️", "done": "✅", "error": "❌", "cancelled": "🚫"}


class SliderSpin(QWidget):
    """Slider + spinbox pair that stay in sync. Works for int and float."""
    valueChanged = Signal(float)

    def __init__(self, minimum=0.0, maximum=100.0, value=0.0, step=1.0,
                 decimals: int = 0, parent=None):
        super().__init__(parent)
        self._scale = 10 ** max(decimals, 0)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(int(minimum * self._scale), int(maximum * self._scale))
        if decimals:
            self.spin = QDoubleSpinBox()
            self.spin.setDecimals(decimals)
        else:
            self.spin = QSpinBox()
        self.spin.setRange(int(minimum) if not decimals else minimum,
                           int(maximum) if not decimals else maximum)
        self.spin.setSingleStep(step)
        self.spin.setFixedWidth(74)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.spin)
        self.slider.valueChanged.connect(self._from_slider)
        self.spin.valueChanged.connect(self._from_spin)
        self.set_value(value)

    def _from_slider(self, v: int):
        val = v / self._scale
        if float(self.spin.value()) != val:
            self.spin.blockSignals(True)
            self.spin.setValue(val)
            self.spin.blockSignals(False)
        self.valueChanged.emit(val)

    def _from_spin(self, v):
        iv = int(float(v) * self._scale)
        if self.slider.value() != iv:
            self.slider.blockSignals(True)
            self.slider.setValue(iv)
            self.slider.blockSignals(False)
        self.valueChanged.emit(float(v))

    def value(self) -> float:
        return float(self.spin.value())

    def set_value(self, v: float):
        self.spin.setValue(v)
        self.slider.setValue(int(float(v) * self._scale))


class CollapsibleSection(QWidget):
    def __init__(self, title: str, content: QWidget, expanded: bool = True, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.btn = QToolButton(text=title, checkable=True, checked=expanded)
        self.btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.btn.setStyleSheet("QToolButton{font-weight:bold;border:none;text-align:left;}")
        self.content = content
        content.setVisible(expanded)
        lay.addWidget(self.btn)
        lay.addWidget(content)
        self.btn.toggled.connect(self._toggle)

    def _toggle(self, on: bool):
        self.content.setVisible(on)
        self.btn.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)


class HLine(QFrame):
    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.Shape.HLine)
        self.setObjectName("hLine")


class ModelCombo(QComboBox):
    """Model selector populated from the registry, grouped by provider, with a
    key-presence marker. THE 'toggle selector' used by chat/generate panels.

    allow_none inserts a real "None" choice with empty data ("" - the same
    sentinel the settings layer already uses for "no model remembered yet"),
    for optional stages like movie-pipeline lip-sync where skipping the
    capability entirely is a valid, common choice, not just an unset default."""

    def __init__(self, registry: Registry, settings, caps: tuple[str, ...],
                 role: str = "", parent=None, allow_none: bool = False,
                 none_label: str = "— None —"):
        super().__init__(parent)
        self.registry = registry
        self.settings = settings
        self.caps = caps
        self.role = role
        self.allow_none = allow_none
        self.none_label = none_label
        self.setMinimumWidth(230)
        self.refresh()
        self.currentIndexChanged.connect(self._remember)

    def refresh(self):
        current = self.current_key() or (self.settings.default_model(self.role) if self.role else "")
        self.blockSignals(True)
        self.clear()
        if self.allow_none:
            self.addItem(self.none_label, "")
        last_provider = None
        for m in self.registry.models_with(*self.caps):
            prov = self.registry.provider(m.provider)
            has_key = bool(self.settings.get_key(m.provider, prov.key_env if prov else ""))
            if m.provider != last_provider:
                self.insertSeparator(self.count()) if self.count() else None
                last_provider = m.provider
            mark = "🔑 " if has_key else "○ "
            rate = cost_estimator.format_rate(m)
            price_suffix = f"  ({rate})" if rate else ""
            self.addItem(f"{mark}{(prov.label if prov else m.provider)} · {m.display}"
                        f"{price_suffix}", m.key)
        idx = self.findData(current)
        if idx >= 0:
            self.setCurrentIndex(idx)
        elif not self.allow_none:  # prefer first model whose provider has a key
            for i in range(self.count()):
                if str(self.itemText(i)).startswith("🔑"):
                    self.setCurrentIndex(i)
                    break
        self.blockSignals(False)

    def _remember(self):
        if self.role and self.current_key():
            self.settings.set_default_model(self.role, self.current_key())

    def current_key(self) -> str:
        return self.currentData() or ""

    def current_model(self) -> ModelSpec | None:
        key = self.current_key()
        return self.registry.by_key(key) if key else None


def label(text: str, dim: bool = False, h1: bool = False) -> QLabel:
    lb = QLabel(text)
    if dim:
        lb.setProperty("dim", "true")
    if h1:
        lb.setProperty("h1", "true")
    lb.setWordWrap(True)
    return lb


def accent_button(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setProperty("accent", "true")
    return b


def browse_row(line_edit: QLineEdit, button: QPushButton) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(line_edit, 1)
    lay.addWidget(button)
    return w


# ---------------------------------------------------------------- toasts

class Toast(QFrame):
    """One transient, non-blocking notification. kind is 'info'|'success'|
    'error' - styled via the Toast[kind=...] QSS rules in theme.py."""

    def __init__(self, text: str, kind: str = "info", parent=None):
        super().__init__(parent)
        self.setProperty("kind", kind)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 6, 8)
        lb = label(text)
        lay.addWidget(lb, 1)
        close = QToolButton(text="✕")
        close.setFixedSize(20, 20)
        close.setToolTip("Dismiss")
        close.setAccessibleName("Dismiss notification")
        close.clicked.connect(self.deleteLater)
        lay.addWidget(close)


class ToastOverlay(QWidget):
    """One per top-level window (e.g. MainWindow). Stacks Toasts bottom-
    right of `host`, sized to its own content so it never intercepts clicks
    over unrelated UI when no toast is showing."""

    def __init__(self, host: QWidget):
        super().__init__(host)
        self.host = host
        self.setObjectName("toastOverlay")
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(6)
        host.installEventFilter(self)
        self.hide()

    def eventFilter(self, obj, ev):
        if obj is self.host and ev.type() == QEvent.Type.Resize:
            self._reposition()
        return False

    def _reposition(self):
        self.adjustSize()
        w = min(360, max(260, self.host.width() // 3))
        h = max(self.sizeHint().height(), 1)
        self.setGeometry(self.host.width() - w - 14, self.host.height() - h - 14, w, h)

    def show_toast(self, text: str, kind: str = "info", timeout_ms: int = 5000) -> Toast:
        t = Toast(text, kind, self)
        self._lay.addWidget(t)
        self.show()
        self._reposition()
        self.raise_()
        if timeout_ms > 0:
            QTimer.singleShot(timeout_ms, lambda: self._remove(t))
        return t

    def _remove(self, t: Toast):
        if self._lay.indexOf(t) < 0:
            return
        self._lay.removeWidget(t)
        t.deleteLater()
        if self._lay.count() == 0:
            self.hide()
        else:
            QTimer.singleShot(0, self._reposition)


# ------------------------------------------------------- confirm + suppress

def confirm_destructive(parent, settings, action_key: str, title: str, text: str,
                        ok_text: str = "Delete") -> bool:
    """Blocking Yes/Cancel confirmation with a persistent 'Don't ask again'.
    action_key must be unique per distinct action - suppressing one action
    must never silently suppress a different one. Reserve this for actions
    OUTSIDE the undo stack (the undo stack already makes in-project deletes
    trivially reversible via Ctrl+Z)."""
    key = f"confirm/suppress/{action_key}"
    if settings.get_bool(key, False):
        return True
    box = QMessageBox(QMessageBox.Icon.Warning, title, text,
                      QMessageBox.StandardButton.Cancel, parent)
    ok_btn = box.addButton(ok_text, QMessageBox.ButtonRole.DestructiveRole)
    dont_ask = QCheckBox("Don't ask again")
    box.setCheckBox(dont_ask)
    box.exec()
    proceed = box.clickedButton() is ok_btn
    if proceed and dont_ask.isChecked():
        settings.set(key, True)
    return proceed


# ----------------------------------------------------------------- drag&drop

class DropAcceptor(QObject):
    """Install on any QWidget (plain widget or QGraphicsView alike) to
    accept OS file drops without editing that widget's own event handlers -
    an event filter, not a mixin, since Python multiple inheritance across
    unrelated Qt base classes is fragile for overriding drag/drop virtuals.

    kinds: subset of ('image','video','audio') per core.media - checked via
    media.is_accepted(). on_files(list[str]) receives accepted paths;
    on_rejected(list[str]), if given, receives rejected ones (e.g. to show
    a toast explaining why)."""

    def __init__(self, target: QWidget, kinds: tuple, on_files, on_rejected=None, parent=None):
        super().__init__(parent or target)
        self.kinds = kinds
        self.on_files = on_files
        self.on_rejected = on_rejected
        self.target = target
        target.setAcceptDrops(True)
        target.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if obj is not self.target:
            return False
        if ev.type() == QEvent.Type.DragEnter:
            if ev.mimeData().hasUrls():
                ev.acceptProposedAction()
                return True
            return False
        if ev.type() == QEvent.Type.Drop:
            paths = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
            good = [p for p in paths if media_utils.is_accepted(p, *self.kinds)]
            bad = [p for p in paths if p not in good]
            if good:
                self.on_files(good)
            if bad and self.on_rejected:
                self.on_rejected(bad)
            ev.acceptProposedAction()
            return True
        return False


# ------------------------------------------------------------- spell check

class SpellCheckHighlighter(QSyntaxHighlighter):
    """Red squiggly underline under misspelled words - attach with
    SpellCheckHighlighter(widget.document()). core.spellcheck soft-fails
    to no results if pyspellchecker isn't available, so this is safe to
    wire onto any QTextEdit/QPlainTextEdit unconditionally rather than
    every call site checking availability itself."""

    def __init__(self, document):
        super().__init__(document)
        self._fmt = QTextCharFormat()
        self._fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SpellCheckUnderline)
        self._fmt.setUnderlineColor(QColor("#e05252"))

    def highlightBlock(self, text: str):
        for start, end in spellcheck.misspelled_spans(text):
            self.setFormat(start, end - start, self._fmt)
