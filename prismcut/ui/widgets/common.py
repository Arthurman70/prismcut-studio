"""Small reusable widgets."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QSlider, QSpinBox, QToolButton,
                               QVBoxLayout, QWidget)

from ...core.registry import ModelSpec, Registry


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
        self.setStyleSheet("color:#3a3f46;")


class ModelCombo(QComboBox):
    """Model selector populated from the registry, grouped by provider, with a
    key-presence marker. THE 'toggle selector' used by chat/generate panels."""

    def __init__(self, registry: Registry, settings, caps: tuple[str, ...],
                 role: str = "", parent=None):
        super().__init__(parent)
        self.registry = registry
        self.settings = settings
        self.caps = caps
        self.role = role
        self.setMinimumWidth(230)
        self.refresh()
        self.currentIndexChanged.connect(self._remember)

    def refresh(self):
        current = self.current_key() or (self.settings.default_model(self.role) if self.role else "")
        self.blockSignals(True)
        self.clear()
        last_provider = None
        for m in self.registry.models_with(*self.caps):
            prov = self.registry.provider(m.provider)
            has_key = bool(self.settings.get_key(m.provider, prov.key_env if prov else ""))
            if m.provider != last_provider:
                self.insertSeparator(self.count()) if self.count() else None
                last_provider = m.provider
            mark = "🔑 " if has_key else "○ "
            self.addItem(f"{mark}{(prov.label if prov else m.provider)} · {m.display}", m.key)
        idx = self.findData(current)
        if idx >= 0:
            self.setCurrentIndex(idx)
        else:  # prefer first model whose provider has a key
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
