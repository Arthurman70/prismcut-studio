"""Generate panel: text -> image / video / music / SFX / speech with full
per-model parameter control (forms are built dynamically from the registry)."""
from __future__ import annotations

import json
import time
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QScrollArea, QSpinBox,
                               QVBoxLayout, QWidget)

from ...core import media as media_utils
from ...core import paths
from ...core.registry import ModelSpec
from ..widgets.common import DropAcceptor, ModelCombo, accent_button, label

MODES = [
    ("Image", ("image_generate",), "image"),
    ("Video", ("video_generate",), "video"),
    ("Music", ("music",), "music"),
    ("SFX", ("sfx",), "sfx"),
    ("Speech", ("tts",), "tts"),
]


class ParamForm(QWidget):
    """Auto-built form for a model's parameter schema."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.form = QFormLayout(self)
        self.form.setContentsMargins(0, 0, 0, 0)
        self.widgets: dict[str, QWidget] = {}

    def build(self, spec: ModelSpec | None, overrides: dict | None = None):
        """overrides pre-fills fields from a saved dict (e.g. a per-scene
        Scene.image_params/video_params override) instead of the model's
        bare defaults - same param names, so a plain dict.get(name, default)
        per field is all that's needed."""
        while self.form.rowCount():
            self.form.removeRow(0)
        self.widgets.clear()
        overrides = overrides or {}
        for p in (spec.params if spec else []):
            t = p.get("type", "text")
            name = p["name"]
            if t == "int":
                w = QSpinBox()
                w.setRange(int(p.get("min", -1)), int(p.get("max", 1 << 31 - 1)))
                w.setValue(int(overrides.get(name, p.get("default", 0))))
            elif t == "float":
                w = QDoubleSpinBox()
                w.setRange(float(p.get("min", 0)), float(p.get("max", 100)))
                w.setSingleStep(float(p.get("step", 0.1)))
                w.setValue(float(overrides.get(name, p.get("default", 0))))
            elif t == "choice":
                w = QComboBox()
                for c in p.get("choices", []):
                    w.addItem(str(c))
                w.setCurrentText(str(overrides.get(name, p.get("default", ""))))
                w.setEditable(True)
            elif t == "bool":
                w = QCheckBox()
                w.setChecked(bool(overrides.get(name, p.get("default", False))))
            else:
                w = QLineEdit(str(overrides.get(name, p.get("default", ""))))
            self.widgets[name] = w
            self.form.addRow(p.get("label", name), w)

    def values(self) -> dict:
        out = {}
        for name, w in self.widgets.items():
            if isinstance(w, QSpinBox):
                out[name] = w.value()
            elif isinstance(w, QDoubleSpinBox):
                out[name] = w.value()
            elif isinstance(w, QComboBox):
                out[name] = w.currentText()
            elif isinstance(w, QCheckBox):
                out[name] = w.isChecked()
            else:
                out[name] = w.text()
        return out


class GeneratePanel(QWidget):
    resultReady = Signal(str, dict)     # path, meta
    status = Signal(str)

    def __init__(self, registry, settings, jobs, get_adapter, parent=None):
        super().__init__(parent)
        self.registry = registry
        self.settings = settings
        self.jobs = jobs
        self.get_adapter = get_adapter
        self.references: list[str] = []

        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        mrow = QHBoxLayout()
        mrow.addWidget(QLabel("Mode"))
        self.mode = QComboBox()
        for name, _caps, _role in MODES:
            self.mode.addItem(name)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        mrow.addWidget(self.mode, 1)
        lay.addLayout(mrow)

        lay.addWidget(label("Model", dim=True))
        self.model_combo = ModelCombo(registry, settings, ("image_generate",),
                                      role="gen_image")
        self.model_combo.currentIndexChanged.connect(self._model_changed)
        lay.addWidget(self.model_combo)

        lay.addWidget(label("Prompt", dim=True))
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText("Describe what to generate…")
        self.prompt.setMinimumHeight(84)
        lay.addWidget(self.prompt)

        erow = QHBoxLayout()
        enhance = QPushButton("✨ Enhance prompt (AI)")
        enhance.setToolTip("Rewrites the prompt with rich cinematic detail using your default chat model")
        enhance.clicked.connect(self._enhance)
        erow.addWidget(enhance)
        erow.addStretch(1)
        lay.addLayout(erow)

        self.param_box = ParamForm()
        lay.addWidget(label("Model parameters", dim=True))
        lay.addWidget(self.param_box)

        # reference images (image->image / image->video / first frame)
        self.ref_label = label("Reference / first-frame images", dim=True)
        lay.addWidget(self.ref_label)
        rrow = QHBoxLayout()
        self.ref_list = QLabel("none")
        self.ref_list.setWordWrap(True)
        add_ref = QPushButton("＋ file")
        add_ref.clicked.connect(self._add_ref_dialog)
        clr_ref = QPushButton("✕")
        clr_ref.setFixedWidth(30)
        clr_ref.clicked.connect(self._clear_refs)
        rrow.addWidget(self.ref_list, 1)
        rrow.addWidget(add_ref)
        rrow.addWidget(clr_ref)
        lay.addLayout(rrow)

        brow = QHBoxLayout()
        brow.addWidget(QLabel("Batch"))
        self.batch = QSpinBox()
        self.batch.setRange(1, 8)
        self.batch.setValue(1)
        brow.addWidget(self.batch)
        brow.addStretch(1)
        lay.addLayout(brow)

        self.go = accent_button("🚀 Generate")
        self.go.clicked.connect(self._generate)
        lay.addWidget(self.go)
        lay.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidget(host)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)

        DropAcceptor(self, ("image",), lambda paths_: [self.add_reference(p) for p in paths_],
                    on_rejected=lambda bad: self.status.emit(
                        f"Only images can be used as a reference - skipped {len(bad)} file(s)."))

        self._mode_changed(0)

    # ---------------------------------------------------------------- state
    def _current_mode(self):
        return MODES[self.mode.currentIndex()]

    def _mode_changed(self, _idx):
        name, caps, role = self._current_mode()
        self.model_combo.caps = caps
        self.model_combo.role = f"gen_{role}"
        self.model_combo.refresh()
        self._model_changed()
        is_visual = name in ("Image", "Video")
        self.ref_label.setVisible(is_visual)
        self.batch.setEnabled(name == "Image")
        ph = {"Image": "Describe the image…  (tip: use Prompt Lab for templates & tuning)",
              "Video": "Describe the shot: subject, action, camera movement, mood…",
              "Music": "Describe the track: genre, mood, tempo, instruments…",
              "SFX": "Describe the sound effect: e.g. heavy wooden door creaking open",
              "Speech": "Enter the text to speak…"}[name]
        self.prompt.setPlaceholderText(ph)

    def _model_changed(self, _idx: int = 0):
        self.param_box.build(self.model_combo.current_model())

    # ------------------------------------------------------------- references
    def add_reference(self, path: str):
        if path and Path(path).exists():
            self.references.append(path)
            self._sync_refs()

    def _add_ref_dialog(self):
        f, _ = QFileDialog.getOpenFileName(self, "Reference image", "", media_utils.IMAGE_FILTER)
        if f:
            self.add_reference(f)

    def _clear_refs(self):
        self.references = []
        self._sync_refs()

    def _sync_refs(self):
        self.ref_list.setText(", ".join(Path(r).name for r in self.references) or "none")

    def set_prompt(self, text: str):
        self.prompt.setPlainText(text)

    # ---------------------------------------------------------------- enhance
    def _enhance(self):
        text = self.prompt.toPlainText().strip()
        if not text:
            return
        chat_key = self.settings.default_model("chat")
        model = self.registry.by_key(chat_key) if chat_key else None
        if model is None:
            candidates = self.registry.models_with("chat")
            model = candidates[0] if candidates else None
        if model is None:
            self.status.emit("No chat model available for enhancement.")
            return
        adapter = self.get_adapter(model.provider)
        mode_name = self._current_mode()[0].lower()
        sys_prompt = (f"You are a prompt engineer for AI {mode_name} generation. Rewrite the "
                      "user's prompt into one richly detailed generation prompt (subject, "
                      "environment, lighting, camera/lens or instrumentation, style, mood). "
                      "Output ONLY the rewritten prompt, no commentary.")

        from ...providers.base import ChatMessage

        def work(job):
            job.progress(-1, "Enhancing prompt")
            return adapter.chat(model.id, [ChatMessage("user", text)], system=sys_prompt,
                                temperature=0.8)

        self.jobs.submit("Enhance prompt", work,
                         on_done=lambda result: (self.prompt.setPlainText(str(result).strip()),
                                                 self.status.emit("Prompt enhanced ✨")),
                         on_fail=lambda m: self.status.emit(f"Enhance failed: {m}"))

    # ---------------------------------------------------------------- generate
    def _generate(self):
        name, _caps, _role = self._current_mode()
        model = self.model_combo.current_model()
        prompt = self.prompt.toPlainText().strip()
        if model is None:
            self.status.emit("Pick a model (add API keys under AI ▸ API Keys…).")
            return
        if not prompt:
            self.status.emit("Write a prompt first.")
            return
        params = self.param_box.values()
        adapter = self.get_adapter(model.provider)
        refs = list(self.references)
        self._log_history(name, model, prompt, params)
        runs = self.batch.value() if name == "Image" else 1

        for i in range(runs):
            run_params = dict(params)
            if runs > 1 and "seed" in run_params:
                try:
                    seed = int(run_params["seed"])
                    if seed >= 0:
                        run_params["seed"] = seed + i
                except (TypeError, ValueError):
                    pass
            meta = {"provider": model.provider, "model": model.id,
                    "prompt": prompt, "params": run_params, "mode": name.lower()}

            def make_work(rp):
                def work(job):
                    job.progress(-1, f"{model.display}")
                    if name == "Image":
                        return adapter.generate_image(model.id, prompt, rp, refs or None)
                    if name == "Video":
                        img = refs[0] if refs else None
                        return [adapter.generate_video(model.id, prompt, rp, img,
                                                       progress=job.progress,
                                                       should_cancel=lambda: job.cancelled)]
                    if name == "Music":
                        return [adapter.music(model.id, prompt, rp,
                                              progress=job.progress,
                                              should_cancel=lambda: job.cancelled)]
                    if name == "SFX":
                        return [adapter.sound_effect(model.id, prompt, rp)]
                    voice = str(rp.get("voice", ""))
                    return [adapter.tts(model.id, prompt, voice, rp)]
                return work

            def done(result, m=meta):
                for out in result or []:
                    self.resultReady.emit(str(out), m)
                self.status.emit(f"✓ {name} ready · {model.display}")

            self.jobs.submit(f"{name}: {prompt[:38]}… · {model.display}",
                             make_work(run_params), kind=name.lower(),
                             on_done=done,
                             on_fail=lambda msg: self.status.emit(f"✗ {name} failed: {msg}"))
        self.status.emit(f"Queued {runs} {name.lower()} job(s) → see Jobs panel")

    def _log_history(self, mode, model, prompt, params):
        try:
            p = paths.prompt_history_path()
            hist = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
            hist.append({"ts": time.time(), "mode": mode, "model": model.key,
                         "prompt": prompt, "params": params})
            p.write_text(json.dumps(hist[-500:], indent=1), encoding="utf-8")
        except Exception:
            pass
