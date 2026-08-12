"""Effects panel: per-clip effect stack applied at export via ffmpeg filters."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ...core.project import Project
from ...core.undo_commands import ChangePropertiesCommand
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

CHROMA_KEY_COLORS = [("Green screen", "#00ff00"), ("Blue screen", "#0000ff")]
CHROMA_KEY_EFFECT_KEYS = ("chroma_key", "chroma_key_color", "chroma_key_similarity", "chroma_key_blend")


class EffectsPanel(QWidget):
    effectsChanged = Signal()

    def __init__(self, project: Project, undo_stack, parent=None):
        super().__init__(parent)
        self.project = project
        self.undo_stack = undo_stack
        self.clip_id: str | None = None
        self._loading = False
        self._gesture_before: dict | None = None

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
            s.slider.sliderReleased.connect(self._commit_gesture)
            s.spin.editingFinished.connect(self._commit_gesture)
            self.sliders[key] = s
            lay.addWidget(s)

        lay.addWidget(label("Chroma key", h1=True))
        self.chroma_check = QCheckBox("Remove background (green/blue screen)")
        # Unlike the sliders (drag = _changed on every tick, release =
        # _commit_gesture once), a checkbox click IS the whole gesture in
        # one signal - both must fire here, in this order, so the mutation
        # happens before _commit_gesture() diffs against it.
        self.chroma_check.toggled.connect(self._chroma_changed)
        self.chroma_check.toggled.connect(self._commit_gesture)
        lay.addWidget(self.chroma_check)
        self.chroma_color = QComboBox()
        for name, _hex in CHROMA_KEY_COLORS:
            self.chroma_color.addItem(name)
        self.chroma_color.currentIndexChanged.connect(self._chroma_changed)
        self.chroma_color.currentIndexChanged.connect(self._commit_gesture)
        lay.addWidget(self.chroma_color)
        lay.addWidget(QLabel("Similarity"))
        self.chroma_similarity = SliderSpin(0, 100, 20, decimals=0)
        self.chroma_similarity.valueChanged.connect(self._chroma_changed)
        self.chroma_similarity.slider.sliderReleased.connect(self._commit_gesture)
        self.chroma_similarity.spin.editingFinished.connect(self._commit_gesture)
        lay.addWidget(self.chroma_similarity)
        lay.addWidget(QLabel("Edge blend"))
        self.chroma_blend = SliderSpin(0, 100, 10, decimals=0)
        self.chroma_blend.valueChanged.connect(self._chroma_changed)
        self.chroma_blend.slider.sliderReleased.connect(self._commit_gesture)
        self.chroma_blend.spin.editingFinished.connect(self._commit_gesture)
        lay.addWidget(self.chroma_blend)

        lay.addWidget(label("LUT (color look)", h1=True))
        lut_row = QHBoxLayout()
        self.lut_label = QLabel("No LUT")
        self.lut_label.setWordWrap(True)
        self.lut_load_btn = QPushButton("Load LUT…")
        self.lut_load_btn.clicked.connect(self._load_lut)
        self.lut_clear_btn = QPushButton("Clear")
        self.lut_clear_btn.clicked.connect(self._clear_lut)
        lut_row.addWidget(self.lut_label, 1)
        lut_row.addWidget(self.lut_load_btn)
        lut_row.addWidget(self.lut_clear_btn)
        lay.addLayout(lut_row)
        lay.addWidget(label("Applies a .cube 3D LUT on export - not a managed library, "
                            "just the one file you pick.", dim=True))

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
        self.chroma_check.setEnabled(on)
        self._update_chroma_enabled()
        self.lut_load_btn.setEnabled(on)
        self.lut_clear_btn.setEnabled(on)

    def _update_chroma_enabled(self):
        on = self.chroma_check.isEnabled() and self.chroma_check.isChecked()
        self.chroma_color.setEnabled(on)
        self.chroma_similarity.setEnabled(on)
        self.chroma_blend.setEnabled(on)

    def show_clip(self, clip_id: str | None):
        # Switching the selected clip mid-gesture (e.g. clicking a different
        # clip on the timeline before releasing the slider/committing a
        # spinbox edit) must not let a stale before-snapshot from the OLD
        # clip leak into a diff computed against the NEW clip's effects.
        self._gesture_before = None
        self.clip_id = clip_id
        clip = self.project.clips.get(clip_id) if clip_id else None
        if not clip:
            self.title.setText("Select a clip in the timeline to edit its effects.")
            self._set_enabled(False)
            self._update_lut_label()
            return
        self.title.setText(f"Effects · {clip.label or 'clip'}")
        self._set_enabled(True)
        self._loading = True
        defaults = {k: d for k, _n, _lo, _hi, d, _dec in EFFECTS}
        for key, slider in self.sliders.items():
            slider.set_value(float(clip.effects.get(key, defaults[key])))
        self.chroma_check.setChecked(bool(clip.effects.get("chroma_key", False)))
        color = clip.effects.get("chroma_key_color", CHROMA_KEY_COLORS[0][1])
        color_idx = next((i for i, (_n, hexval) in enumerate(CHROMA_KEY_COLORS)
                          if hexval == color), 0)
        self.chroma_color.setCurrentIndex(color_idx)
        self.chroma_similarity.set_value(float(clip.effects.get("chroma_key_similarity", 20)))
        self.chroma_blend.set_value(float(clip.effects.get("chroma_key_blend", 10)))
        self._loading = False
        self._update_chroma_enabled()
        self._update_lut_label()

    def _changed(self, *_a):
        """Live-updates clip.effects on every tick for immediate visual
        feedback (unchanged from before) - undo commands are pushed
        separately, once per gesture, by _commit_gesture(). Lazily snapshots
        the pre-gesture state on whichever interaction happens first
        (slider drag OR spinbox edit) instead of only ever priming on
        sliderPressed - a spinbox-only edit (typing a value, or clicking
        its arrows, without ever touching the slider handle) used to leave
        _gesture_before permanently None, so _commit_gesture silently
        skipped pushing an undo entry for it."""
        if self._loading or not self.clip_id:
            return
        clip = self.project.clips.get(self.clip_id)
        if not clip:
            return
        if self._gesture_before is None:
            self._gesture_before = dict(clip.effects)
        defaults = {k: d for k, _n, _lo, _hi, d, _dec in EFFECTS}
        # Keys this method doesn't own (chroma key's, set by _chroma_changed)
        # are preserved rather than dropped - each handler only ever
        # rebuilds the slice of clip.effects its own widgets are
        # responsible for.
        fx = {k: v for k, v in clip.effects.items() if k not in self.sliders}
        fx.update({k: s.value() for k, s in self.sliders.items()
                  if abs(s.value() - defaults[k]) > 1e-9})
        clip.effects = fx
        self.project.dirty = True
        self.effectsChanged.emit()

    def _chroma_changed(self, *_a):
        if self._loading or not self.clip_id:
            return
        clip = self.project.clips.get(self.clip_id)
        if not clip:
            return
        self._update_chroma_enabled()
        if self._gesture_before is None:
            self._gesture_before = dict(clip.effects)
        fx = {k: v for k, v in clip.effects.items() if k not in CHROMA_KEY_EFFECT_KEYS}
        if self.chroma_check.isChecked():
            fx["chroma_key"] = True
            fx["chroma_key_color"] = CHROMA_KEY_COLORS[self.chroma_color.currentIndex()][1]
            fx["chroma_key_similarity"] = self.chroma_similarity.value()
            fx["chroma_key_blend"] = self.chroma_blend.value()
        clip.effects = fx
        self.project.dirty = True
        self.effectsChanged.emit()

    def _update_lut_label(self):
        clip = self.project.clips.get(self.clip_id) if self.clip_id else None
        path = clip.effects.get("lut_path") if clip else None
        self.lut_label.setText(Path(path).name if path else "No LUT")

    def _lut_refresh(self):
        self.project.dirty = True
        self._update_lut_label()
        self.effectsChanged.emit()

    def _load_lut(self):
        clip = self.project.clips.get(self.clip_id) if self.clip_id else None
        if not clip:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Load LUT", "", "3D LUT files (*.cube *.3dl *.dat *.m3d *.csp)")
        if not path:
            return
        before = dict(clip.effects)
        after = dict(clip.effects)
        after["lut_path"] = path
        self.undo_stack.push(ChangePropertiesCommand(
            f"Apply LUT · {Path(path).name}", clip,
            {"effects": before}, {"effects": after}, self._lut_refresh))

    def _clear_lut(self):
        clip = self.project.clips.get(self.clip_id) if self.clip_id else None
        if not clip or "lut_path" not in clip.effects:
            return
        before = dict(clip.effects)
        after = {k: v for k, v in clip.effects.items() if k != "lut_path"}
        self.undo_stack.push(ChangePropertiesCommand(
            "Remove LUT", clip, {"effects": before}, {"effects": after}, self._lut_refresh))

    def _commit_gesture(self):
        clip = self.project.clips.get(self.clip_id) if self.clip_id else None
        if not clip or self._gesture_before is None:
            self._gesture_before = None
            return
        before, after = self._gesture_before, dict(clip.effects)
        self._gesture_before = None
        if before != after:
            self.undo_stack.push(ChangePropertiesCommand(
                f"Adjust effects · {clip.label or 'clip'}", clip,
                {"effects": before}, {"effects": after}, self.effectsChanged.emit))

    def _reset(self):
        clip = self.project.clips.get(self.clip_id) if self.clip_id else None
        before = dict(clip.effects) if clip else None
        defaults = {k: d for k, _n, _lo, _hi, d, _dec in EFFECTS}
        self._loading = True
        for k, s in self.sliders.items():
            s.set_value(defaults[k])
        self.chroma_check.setChecked(False)
        self.chroma_color.setCurrentIndex(0)
        self.chroma_similarity.set_value(20)
        self.chroma_blend.set_value(10)
        self._loading = False
        self._update_chroma_enabled()
        self._changed()
        self._chroma_changed()
        if clip is not None and "lut_path" in clip.effects:
            clip.effects = {k: v for k, v in clip.effects.items() if k != "lut_path"}
        self._update_lut_label()
        if clip is not None and before != clip.effects:
            self.undo_stack.push(ChangePropertiesCommand(
                f"Reset effects · {clip.label or 'clip'}", clip,
                {"effects": before}, {"effects": dict(clip.effects)}, self.effectsChanged.emit))
        # Both _changed()/_chroma_changed() above prime _gesture_before but
        # nothing calls _commit_gesture() to clear it after a Reset click
        # (this method already pushes its own undo command instead) -
        # leaving it dangling would corrupt the "before" snapshot of
        # whichever slider/checkbox gesture happens next.
        self._gesture_before = None
