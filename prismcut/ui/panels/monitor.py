"""Clip & Project monitors (Kdenlive's dual-monitor layout).

QtMultimedia is optional: when the platform backend is unavailable the monitor
degrades to image/waveform preview instead of crashing.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSlider,
                               QStackedLayout, QVBoxLayout, QWidget)

from ...core import media as media_utils
from ...core.project import Project

try:  # pragma: no cover - environment dependent
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget
    HAVE_MM = True
except Exception:  # pragma: no cover
    HAVE_MM = False


def _fmt(t: float) -> str:
    t = max(0.0, t)
    m, s = divmod(int(t), 60)
    return f"{m:02d}:{s:02d}.{int((t - int(t)) * 10)}"


class MonitorBase(QWidget):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(2)
        cap = QLabel(title)
        cap.setStyleSheet("font-weight:bold;color:#8b929b;padding:1px 4px;")
        outer.addWidget(cap)

        self.stack_host = QWidget()
        self.stack_host.setStyleSheet("background:#111;border:1px solid #3a3f46;")
        self.stack = QStackedLayout(self.stack_host)
        self.image_label = QLabel("No media")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setStyleSheet("color:#666;")
        self.image_label.setScaledContents(False)
        self.stack.addWidget(self.image_label)

        self.player = None
        self.video_widget = None
        if HAVE_MM:
            try:
                self.video_widget = QVideoWidget()
                self.player = QMediaPlayer()
                self.audio_out = QAudioOutput()
                self.player.setAudioOutput(self.audio_out)
                self.player.setVideoOutput(self.video_widget)
                self.stack.addWidget(self.video_widget)
                self.player.positionChanged.connect(self._pos_changed)
                self.player.durationChanged.connect(self._dur_changed)
            except Exception:
                self.player = None
        outer.addWidget(self.stack_host, 1)

        bar = QHBoxLayout()
        self.btn_play = QPushButton("▶")
        self.btn_play.setFixedWidth(36)
        self.btn_play.clicked.connect(self.toggle_play)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.sliderMoved.connect(self._scrub)
        self.time_label = QLabel("00:00.0")
        self.time_label.setFixedWidth(64)
        bar.addWidget(self.btn_play)
        bar.addWidget(self.slider, 1)
        bar.addWidget(self.time_label)
        outer.addLayout(bar)
        self._current_path: str | None = None
        self._pix: QPixmap | None = None

    # ---------------------------------------------------------------- loading
    def show_image(self, path):
        self._current_path = str(path)
        self._pix = QPixmap(str(path))
        self._update_pix()
        self.stack.setCurrentWidget(self.image_label)
        if self.player:
            self.player.stop()

    def _update_pix(self):
        if self._pix and not self._pix.isNull():
            scaled = self._pix.scaled(self.stack_host.size(),
                                      Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation)
            self.image_label.setPixmap(scaled)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._update_pix()

    def show_media(self, path):
        path = str(path)
        kind = media_utils.kind_of(path)
        self._current_path = path
        if kind == "image" or self.player is None:
            if kind in ("video", "audio") and self.player is None:
                wf = media_utils.thumbnail(path)
                if wf:
                    self.show_image(wf)
                else:
                    self.image_label.setText(Path(path).name + "\n(preview backend unavailable)")
                    self.stack.setCurrentWidget(self.image_label)
                return
            self.show_image(path)
            return
        self.player.setSource(QUrl.fromLocalFile(path))
        if kind == "video" and self.video_widget is not None:
            self.stack.setCurrentWidget(self.video_widget)
        else:
            wf = media_utils.thumbnail(path)
            if wf:
                self.show_image(wf)
                self._current_path = path  # keep audio source for playback
                self.player.setSource(QUrl.fromLocalFile(path))
        self.player.pause()

    # --------------------------------------------------------------- playback
    def toggle_play(self):
        if not self.player or not self._current_path:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.btn_play.setText("▶")
        else:
            self.player.play()
            self.btn_play.setText("⏸")

    def _pos_changed(self, ms: int):
        dur = max(1, self.player.duration() if self.player else 1)
        self.slider.blockSignals(True)
        self.slider.setValue(int(ms / dur * 1000))
        self.slider.blockSignals(False)
        self.time_label.setText(_fmt(ms / 1000.0))

    def _dur_changed(self, _ms: int):
        pass

    def _scrub(self, v: int):
        if self.player and self.player.duration() > 0:
            self.player.setPosition(int(v / 1000 * self.player.duration()))

    def seek_seconds(self, t: float):
        if self.player:
            self.player.setPosition(int(t * 1000))


class ClipMonitor(MonitorBase):
    def __init__(self, parent=None):
        super().__init__("Clip Monitor", parent)


class ProjectMonitor(MonitorBase):
    """Previews the timeline at the playhead (topmost clip under the cursor)."""
    requestAddAtPlayhead = Signal()

    def __init__(self, project: Project, parent=None):
        super().__init__("Project Monitor", parent)
        self.project = project
        self._preview_media_id: str | None = None

    def set_project(self, project: Project):
        self.project = project
        self._preview_media_id = None

    def preview_at(self, t: float):
        clip, item, offset = self.project.resolve_at(t)
        self.time_label.setText(_fmt(t))
        if item is None:
            self.image_label.setText("∅  (no clip at playhead)")
            self.image_label.setPixmap(QPixmap())
            self.stack.setCurrentWidget(self.image_label)
            self._preview_media_id = None
            if self.player:
                self.player.pause()
            return
        if item.kind == "image":
            if self._preview_media_id != item.id:
                self.show_image(item.path)
                self._preview_media_id = item.id
        else:
            if self._preview_media_id != item.id:
                self.show_media(item.path)
                self._preview_media_id = item.id
            self.seek_seconds(offset)
