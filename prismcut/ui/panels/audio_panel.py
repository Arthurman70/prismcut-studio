"""Audio Lab: per-track mixer (gain/pan/mute/solo), clip audio tools with
waveform view, and AI audio generation (TTS / music / SFX / transcription)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QComboBox, QDial, QDoubleSpinBox, QGroupBox,
                               QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                               QScrollArea, QSlider, QTabWidget, QToolButton,
                               QVBoxLayout, QWidget)

from ...core import media as media_utils
from ...core.project import Project
from ...core.undo_commands import ChangePropertiesCommand
from .. import theme
from ..widgets.common import DropAcceptor, ModelCombo, accent_button, label


class TrackStrip(QWidget):
    changed = Signal()

    def __init__(self, track, undo_stack, parent=None):
        super().__init__(parent)
        self.track = track
        self.undo_stack = undo_stack
        self._gesture_before: dict | None = None
        self.setFixedWidth(86)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(3)
        name = QLabel(track.name)
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name.setStyleSheet(f"font-weight:bold;color:{theme.ACCENT};")
        lay.addWidget(name)

        self.fader = QSlider(Qt.Orientation.Vertical)
        self.fader.setRange(-60, 6)
        self.fader.setValue(int(track.gain_db))
        self.fader.setMinimumHeight(140)
        self.fader.valueChanged.connect(self._gain)
        self.fader.sliderPressed.connect(self._begin_gesture)
        self.fader.sliderReleased.connect(self._commit_gesture)
        fl = QHBoxLayout()
        fl.addStretch(1)
        fl.addWidget(self.fader)
        fl.addStretch(1)
        lay.addLayout(fl)
        self.gain_label = QLabel(f"{track.gain_db:+.0f} dB")
        self.gain_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.gain_label.setProperty("dim", "true")
        lay.addWidget(self.gain_label)

        self.pan = QDial()
        self.pan.setRange(-100, 100)
        self.pan.setValue(int(track.pan * 100))
        self.pan.setFixedSize(44, 44)
        self.pan.setNotchesVisible(True)
        self.pan.valueChanged.connect(self._pan)
        self.pan.sliderPressed.connect(self._begin_gesture)
        self.pan.sliderReleased.connect(self._commit_gesture)
        pl = QHBoxLayout()
        pl.addStretch(1)
        pl.addWidget(self.pan)
        pl.addStretch(1)
        lay.addLayout(pl)
        lay.addWidget(QLabel("pan", alignment=Qt.AlignmentFlag.AlignCenter))

        brow = QHBoxLayout()
        self.mute = QToolButton(text="M", checkable=True, checked=track.mute)
        self.solo = QToolButton(text="S", checkable=True, checked=track.solo)
        for b, attr, tip in ((self.mute, "mute", "Mute track"), (self.solo, "solo", "Solo track")):
            b.setFixedSize(30, 22)
            b.setToolTip(tip)
            b.toggled.connect(lambda on, a=attr: self._toggle_attr(a, on))
            brow.addWidget(b)
        lay.addLayout(brow)

    def _gain(self, v: int):
        self.track.gain_db = float(v)
        self.gain_label.setText(f"{v:+d} dB")
        self.changed.emit()

    def _pan(self, v: int):
        self.track.pan = v / 100.0
        self.changed.emit()

    def _toggle_attr(self, attr: str, on: bool):
        self.undo_stack.push(ChangePropertiesCommand(
            f"{'Mute' if attr == 'mute' else 'Solo'} {self.track.name}",
            self.track, {attr: not on}, {attr: on}, self.changed.emit))

    def _begin_gesture(self):
        self._gesture_before = {"gain_db": self.track.gain_db, "pan": self.track.pan}

    def _commit_gesture(self):
        if self._gesture_before is None:
            return
        before, after = self._gesture_before, {"gain_db": self.track.gain_db, "pan": self.track.pan}
        self._gesture_before = None
        if before != after:
            self.undo_stack.push(ChangePropertiesCommand(
                f"Adjust {self.track.name} mixer", self.track, before, after, self.changed.emit))


class AudioPanel(QWidget):
    resultReady = Signal(str, dict)      # generated audio path, meta
    timelineChanged = Signal()
    status = Signal(str)
    mediaDropped = Signal(list)          # dropped audio/video paths -> bin

    def __init__(self, project: Project, registry, settings, jobs, get_adapter, undo_stack,
                parent=None):
        super().__init__(parent)
        self.project = project
        self.registry = registry
        self.settings = settings
        self.jobs = jobs
        self.get_adapter = get_adapter
        self.undo_stack = undo_stack
        self.selected_clip_id: str | None = None
        self._clip_gesture_before: dict | None = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)

        # ------------------------------------------------------------ mixer
        mixer_box = QGroupBox("Mixer")
        mv = QVBoxLayout(mixer_box)
        self.strips_host = QWidget()
        self.strips_lay = QHBoxLayout(self.strips_host)
        self.strips_lay.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidget(self.strips_host)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        mv.addWidget(scroll)
        mv.addWidget(label("Faders & pan are applied at export (ffmpeg).", dim=True))
        lay.addWidget(mixer_box, 1)

        # ------------------------------------------------------- clip tools
        right = QTabWidget()
        lay.addWidget(right, 2)

        clip_page = QWidget()
        cv = QVBoxLayout(clip_page)
        self.clip_info = label("Select an audio clip in the timeline…", dim=True)
        cv.addWidget(self.clip_info)
        self.wave = QLabel()
        self.wave.setFixedHeight(110)
        self.wave.setObjectName("audioWave")
        self.wave.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cv.addWidget(self.wave)

        grow = QHBoxLayout()
        grow.addWidget(QLabel("Clip gain (dB)"))
        self.clip_gain = QDoubleSpinBox()
        self.clip_gain.setRange(-60, 12)
        self.clip_gain.valueChanged.connect(self._clip_changed)
        self.clip_gain.editingFinished.connect(self._commit_clip_gesture)
        grow.addWidget(self.clip_gain)
        grow.addWidget(QLabel("Fade in (s)"))
        self.fade_in = QDoubleSpinBox()
        self.fade_in.setRange(0, 30)
        self.fade_in.setSingleStep(0.1)
        self.fade_in.valueChanged.connect(self._clip_changed)
        self.fade_in.editingFinished.connect(self._commit_clip_gesture)
        grow.addWidget(self.fade_in)
        grow.addWidget(QLabel("Fade out (s)"))
        self.fade_out = QDoubleSpinBox()
        self.fade_out.setRange(0, 30)
        self.fade_out.setSingleStep(0.1)
        self.fade_out.valueChanged.connect(self._clip_changed)
        self.fade_out.editingFinished.connect(self._commit_clip_gesture)
        grow.addWidget(self.fade_out)
        grow.addStretch(1)
        cv.addLayout(grow)

        nrow = QHBoxLayout()
        norm = QPushButton("🎚 Normalize to -14 LUFS-ish")
        norm.setToolTip("Measures mean volume with ffmpeg and sets clip gain")
        norm.clicked.connect(self._normalize)
        nrow.addWidget(norm)
        nrow.addStretch(1)
        cv.addLayout(nrow)
        cv.addStretch(1)
        right.addTab(clip_page, "Clip tools")

        # --------------------------------------------------------- AI audio
        ai_page = QWidget()
        av = QVBoxLayout(ai_page)

        self.ai_kind = QComboBox()
        self.ai_kind.addItems(["🎙 Speech (TTS)", "🎵 Music", "💥 Sound effect", "📝 Transcribe"])
        self.ai_kind.currentIndexChanged.connect(self._ai_kind_changed)
        av.addWidget(self.ai_kind)

        self.ai_model = ModelCombo(registry, settings, ("tts",), role="audio_tts")
        av.addWidget(self.ai_model)

        self.ai_voice = QComboBox()
        self.ai_voice.setEditable(True)
        self.ai_voice.setToolTip("Voice (model-specific; for ElevenLabs paste a voice id)")
        av.addWidget(self.ai_voice)

        self.ai_text = QPlainTextEdit()
        self.ai_text.setPlaceholderText("Text to speak…")
        self.ai_text.setMinimumHeight(80)
        av.addWidget(self.ai_text)

        drow = QHBoxLayout()
        drow.addWidget(QLabel("Duration (s)"))
        self.ai_duration = QDoubleSpinBox()
        self.ai_duration.setRange(1, 300)
        self.ai_duration.setValue(30)
        drow.addWidget(self.ai_duration)
        drow.addStretch(1)
        av.addLayout(drow)

        self.ai_go = accent_button("🎧 Generate audio")
        self.ai_go.clicked.connect(self._ai_generate)
        av.addWidget(self.ai_go)
        av.addWidget(label("Generated audio lands in the Project Bin (and can be "
                           "auto-added at the playhead from its context menu).", dim=True))
        av.addStretch(1)
        right.addTab(ai_page, "AI audio")
        self._ai_kind_changed(0)
        self.refresh_tracks()
        # Deliberately just adds to the bin, not auto-transcribing - this is
        # a bring-your-own-key app, so silently spending the user's API
        # credits as a side effect of a drag would be a bad surprise.
        DropAcceptor(self, ("audio", "video"), self.mediaDropped.emit,
                    on_rejected=lambda bad: self.status.emit(
                        f"Audio Lab only accepts audio/video - skipped {len(bad)} file(s)."))

    # ------------------------------------------------------------------ mixer
    def set_project(self, project: Project):
        self.project = project
        self.refresh_tracks()

    def refresh_tracks(self):
        while self.strips_lay.count():
            it = self.strips_lay.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        for tr in self.project.audio_tracks() + self.project.video_tracks():
            strip = TrackStrip(tr, self.undo_stack)
            strip.changed.connect(self.timelineChanged.emit)
            self.strips_lay.addWidget(strip)
        self.strips_lay.addStretch(1)

    # ------------------------------------------------------------- clip tools
    def show_clip(self, clip_id: str | None):
        self.selected_clip_id = clip_id
        clip = self.project.clips.get(clip_id) if clip_id else None
        if not clip:
            self.clip_info.setText("Select an audio clip in the timeline…")
            self.wave.setPixmap(QPixmap())
            self._clip_gesture_before = None
            return
        item = self.project.media.get(clip.media_id)
        self.clip_info.setText(f"🎵 {clip.label}  ·  {clip.duration:.1f}s")
        for w, v in ((self.clip_gain, clip.gain_db), (self.fade_in, clip.fade_in),
                     (self.fade_out, clip.fade_out)):
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)
        self._clip_gesture_before = {"gain_db": clip.gain_db, "fade_in": clip.fade_in,
                                     "fade_out": clip.fade_out}
        if item:
            wf = media_utils.waveform_png(item.path, width=560, height=100)
            if wf:
                self.wave.setPixmap(QPixmap(str(wf)))

    def _clip_changed(self, *_a):
        clip = self.project.clips.get(self.selected_clip_id) if self.selected_clip_id else None
        if clip:
            clip.gain_db = self.clip_gain.value()
            clip.fade_in = self.fade_in.value()
            clip.fade_out = self.fade_out.value()
            self.timelineChanged.emit()

    def _commit_clip_gesture(self):
        clip = self.project.clips.get(self.selected_clip_id) if self.selected_clip_id else None
        if not clip or self._clip_gesture_before is None:
            return
        before = self._clip_gesture_before
        after = {"gain_db": clip.gain_db, "fade_in": clip.fade_in, "fade_out": clip.fade_out}
        self._clip_gesture_before = dict(after)
        if before != after:
            self.undo_stack.push(ChangePropertiesCommand(
                f"Adjust {clip.label or 'clip'} audio", clip, before, after,
                self.timelineChanged.emit))

    def _normalize(self):
        clip = self.project.clips.get(self.selected_clip_id) if self.selected_clip_id else None
        item = self.project.media.get(clip.media_id) if clip else None
        if not item:
            self.status.emit("Select an audio clip first.")
            return

        def work(job):
            job.progress(-1, "Measuring loudness")
            return media_utils.measure_loudness(item.path)

        def done(mean_db):
            if mean_db is None:
                self.status.emit("Could not measure loudness (is ffmpeg installed?)")
                return
            clip.gain_db = max(-60.0, min(12.0, -16.0 - mean_db))
            self.show_clip(clip.id)
            self.timelineChanged.emit()
            self.status.emit(f"Normalized: mean {mean_db:.1f} dB → gain {clip.gain_db:+.1f} dB")

        self.jobs.submit("Normalize audio", work, on_done=done,
                         on_fail=lambda m: self.status.emit(f"Normalize failed: {m}"))

    # --------------------------------------------------------------- AI audio
    def _ai_kind_changed(self, idx: int):
        kind = ["tts", "music", "sfx", "transcribe"][idx]
        caps = {"tts": ("tts",), "music": ("music",), "sfx": ("sfx",),
                "transcribe": ("transcribe",)}[kind]
        self.ai_model.caps = caps
        self.ai_model.role = f"audio_{kind}"
        self.ai_model.refresh()
        self.ai_voice.setVisible(kind == "tts")
        self.ai_duration.setVisible(kind in ("music", "sfx"))
        ph = {"tts": "Text to speak…",
              "music": "Describe the track: genre, mood, tempo, instruments…",
              "sfx": "Describe the sound: e.g. retro arcade power-up chime",
              "transcribe": "Pick an audio/video file below (📝 button) - transcript will "
                            "be saved as a text file in the bin."}[kind]
        self.ai_text.setPlaceholderText(ph)
        self.ai_go.setText({"tts": "🎙 Generate speech", "music": "🎵 Generate music",
                            "sfx": "💥 Generate SFX", "transcribe": "📝 Transcribe file…"}[kind])
        self._fill_voices(kind)

    def _fill_voices(self, kind: str):
        self.ai_voice.clear()
        if kind != "tts":
            return
        model = self.ai_model.current_model()
        if model is None:
            return
        for p in model.params:
            if p["name"] == "voice":
                if p.get("type") == "choice":
                    self.ai_voice.addItems([str(c) for c in p.get("choices", [])])
                else:
                    self.ai_voice.addItem(str(p.get("default", "")))

    def _ai_generate(self):
        idx = self.ai_kind.currentIndex()
        kind = ["tts", "music", "sfx", "transcribe"][idx]
        model = self.ai_model.current_model()
        if model is None:
            self.status.emit("No model with that capability - add API keys first.")
            return
        adapter = self.get_adapter(model.provider)

        if kind == "transcribe":
            from PySide6.QtWidgets import QFileDialog
            f, _ = QFileDialog.getOpenFileName(self, "Transcribe media", "", media_utils.AV_FILTER)
            if not f:
                return

            def work(job):
                job.progress(-1, f"Transcribing via {model.display}")
                src = f
                if Path(f).suffix.lower() in (".ogg", ".flac", ".webm"):
                    src = str(media_utils.convert_audio(f, ".mp3"))
                return adapter.transcribe(model.id, src)

            def done(text):
                from ...core import paths as p_
                out = p_.unique_path(p_.generated_dir(), "transcript", ".txt")
                Path(out).write_text(str(text), encoding="utf-8")
                self.resultReady.emit(str(out), {"mode": "transcript", "model": model.id})
                self.status.emit("Transcript saved to bin ✓")

            self.jobs.submit(f"Transcribe · {model.display}", work, on_done=done,
                             on_fail=lambda m: self.status.emit(f"Transcribe failed: {m}"))
            return

        text = self.ai_text.toPlainText().strip()
        if not text:
            self.status.emit("Write a prompt / text first.")
            return
        voice = self.ai_voice.currentText().strip()
        duration = self.ai_duration.value()
        meta = {"mode": kind, "model": model.id, "provider": model.provider, "prompt": text}

        def work(job):
            job.progress(-1, f"{model.display}")
            if kind == "tts":
                return adapter.tts(model.id, text, voice)
            if kind == "music":
                return adapter.music(model.id, text, {"duration": duration},
                                     progress=job.progress,
                                     should_cancel=lambda: job.cancelled)
            return adapter.sound_effect(model.id, text, {"duration": duration})

        self.jobs.submit(f"{kind.upper()}: {text[:32]}… · {model.display}", work, kind="audio",
                         on_done=lambda out: (self.resultReady.emit(str(out), meta),
                                              self.status.emit(f"✓ {kind} ready")),
                         on_fail=lambda m: self.status.emit(f"✗ {kind} failed: {m}"))
