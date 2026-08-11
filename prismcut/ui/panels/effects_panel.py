"""Effects panel: per-clip effect stack applied at export via ffmpeg filters."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ...core.project import Project
from ..widgets.common import SliderSpin, label

EFFECTS = [
    ("scale_pct", "Scale %", 10, 400, 100, 0),
    ("rotate_deg", "Rotate °", -180, 180, 0, 0),
    ("opacity", "Opacity %", 0, 100, 100, 0),
    ("brightness", "Brightness", -100, 100, 0, 0),
    ("contrast", "Contrast", -100, 100, 0, 0),
    ("saturation", "Saturation", -100, 100, 0, 0),
    ("blur", "Blur", 0, 30, 0, 1),
    ("speed", "Speed ×", 0.25, 4.0, 1.0, 2),
]


class EffectsPanel(QWidget):
    effectsChanged = Signal()

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.clip_id: str | None = None
        self._loading = False

        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(8, 8, 8, 8)
        self.title = label("Select a clip in the timeline to edit its effects.", dim=True)
        lay.addWidget(self.title)
        self.sliders: dict[str, SliderSpin] = {}
        for key, name, lo, hi, default, decimals in EFFECTS:
            lay.addWidget(QLabel(name))
            s = SliderSpin(lo, hi, default, decimals=decimals)
            s.valueChanged.connect(self._changed)
            self.sliders[key] = s
            lay.addWidget(s)
        reset = QPushButton("Reset all effects")
        reset.clicked.connect(self._reset)
        lay.addWidget(reset)
        lay.addWidget(label("Effects render on export (ffmpeg): transform, opacity, "
                            "color, blur and speed. AI-powered restyling of stills "
                            "lives in Photo Studio ▸ Nano Tools.", dim=True))
        lay.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidget(host)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)
        self._set_enabled(False)

    def set_project(self, project: Project):
        self.project = project
        self.show_clip(None)

    def _set_enabled(self, on: bool):
        for s in self.sliders.values():
            s.setEnabled(on)

    def show_clip(self, clip_id: str | None):
        self.clip_id = clip_id
        clip = self.project.clips.get(clip_id) if clip_id else None
        if not clip:
            self.title.setText("Select a clip in the timeline to edit its effects.")
            self._set_enabled(False)
            return
        self.title.setText(f"Effects · {clip.label or 'clip'}")
        self._set_enabled(True)
        self._loading = True
        defaults = {k: d for k, _n, _lo, _hi, d, _dec in EFFECTS}
        for key, slider in self.sliders.items():
            slider.set_value(float(clip.effects.get(key, defaults[key])))
        self._loading = False

    def _changed(self, *_a):
        if self._loading or not self.clip_id:
            return
        clip = self.project.clips.get(self.clip_id)
        if not clip:
            return
        defaults = {k: d for k, _n, _lo, _hi, d, _dec in EFFECTS}
        clip.effects = {k: s.value() for k, s in self.sliders.items()
                        if abs(s.value() - defaults[k]) > 1e-9}
        self.project.dirty = True
        self.effectsChanged.emit()

    def _reset(self):
        defaults = {k: d for k, _n, _lo, _hi, d, _dec in EFFECTS}
        self._loading = True
        for k, s in self.sliders.items():
            s.set_value(defaults[k])
        self._loading = False
        self._changed()
