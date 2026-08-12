"""Clip & Project monitors (Kdenlive's dual-monitor layout).

QtMultimedia is optional: when the platform backend is unavailable the monitor
degrades to image/waveform preview instead of crashing.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
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
                self.player.mediaStatusChanged.connect(self._media_status_changed)
            except Exception:
                self.player = None
        outer.addWidget(self.stack_host, 1)
        # A seek requested before the player has finished opening its new
        # source - see seek_seconds()'s docstring for why this exists.
        self._pending_seek: float | None = None

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

    def show_title(self, meta: dict, canvas_height: int = 1080):
        """Title clips have no real file to decode - a simple QPainter
        render of the same text/size/color/position stands in for a live
        ffmpeg preview here (render.py's drawtext is what actually produces
        the exported frame; this is preview-only)."""
        self._current_path = None
        if self.player:
            self.player.stop()
        size = self.stack_host.size()
        if size.width() < 2 or size.height() < 2:
            size = self.image_label.size()
        pix = QPixmap(size)
        pix.fill(QColor(0, 0, 0, 0))
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont()
        font.setBold(bool(meta.get("bold", True)))
        px = max(8, int(size.height() * (float(meta.get("font_size", 64) or 64) / max(1, canvas_height))))
        font.setPixelSize(px)
        p.setFont(font)
        text = str(meta.get("title_text", ""))
        align = Qt.AlignmentFlag.AlignHCenter | {
            "top": Qt.AlignmentFlag.AlignTop, "bottom": Qt.AlignmentFlag.AlignBottom,
        }.get(meta.get("position", "center"), Qt.AlignmentFlag.AlignVCenter)
        if meta.get("shadow", True):
            p.setPen(QColor(0, 0, 0, 180))
            p.drawText(pix.rect().translated(2, 2), int(align), text)
        p.setPen(QColor(meta.get("color", "#ffffff")))
        p.drawText(pix.rect(), int(align), text)
        p.end()
        self._pix = pix
        self._update_pix()
        self.stack.setCurrentWidget(self.image_label)

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
        """Loading a new source (setSource()) is asynchronous - a
        setPosition() issued before the backend reports the media is
        actually ready is silently dropped on most QtMultimedia backends,
        which is the concrete cause of timeline scrub-preview freezing
        (not displaying/advancing) whenever it crosses into a new scene's
        clip: show_media() sets the new source, then preview_at() calls
        this immediately after, too soon for the seek to take effect. Defer
        to _media_status_changed when that's the case instead of dropping
        the seek on the floor."""
        if not self.player:
            return
        ready = (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia,
                QMediaPlayer.MediaStatus.EndOfMedia)
        if self.player.mediaStatus() in ready:
            self.player.setPosition(int(t * 1000))
            self._pending_seek = None
        else:
            self._pending_seek = t

    def _media_status_changed(self, status):
        if self._pending_seek is None:
            return
        ready = (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia,
                QMediaPlayer.MediaStatus.EndOfMedia)
        if status in ready:
            self.player.setPosition(int(self._pending_seek * 1000))
            self._pending_seek = None


class ClipMonitor(MonitorBase):
    def __init__(self, parent=None):
        super().__init__("Clip Monitor", parent)


class ProjectMonitor(MonitorBase):
    """Previews the timeline at the playhead (topmost clip under the cursor).

    resolve_at() only ever looks at video tracks, so on its own this monitor
    is deaf to anything living on an audio track - a video's auto-split
    audio companion (see Project.split_video_audio), background music,
    narration. A second, audio-only QMediaPlayer, driven by
    Project.resolve_audio_at(), mixes that back in - kept in sync with the
    primary player for play/pause (see toggle_play) since QMediaPlayer has
    no built-in way to gang two players together."""
    requestAddAtPlayhead = Signal()

    def __init__(self, project: Project, parent=None):
        super().__init__("Project Monitor", parent)
        self.project = project
        self._preview_media_id: str | None = None
        self._preview_audio_media_id: str | None = None
        # Preview-only preference (export always renders from MediaItem.path,
        # never proxy_path) - MainWindow sets this from settings at startup
        # and again whenever the View-menu toggle changes.
        self.use_proxy = True
        self.audio_player = None
        if HAVE_MM:
            try:
                self.audio_player = QMediaPlayer()
                self._audio_out2 = QAudioOutput()
                self.audio_player.setAudioOutput(self._audio_out2)
                self.audio_player.mediaStatusChanged.connect(self._audio_media_status_changed)
            except Exception:
                self.audio_player = None
        self._pending_audio_seek: float | None = None

    def set_project(self, project: Project):
        self.project = project
        self._preview_media_id = None
        self._preview_audio_media_id = None

    def toggle_play(self):
        super().toggle_play()
        if self.audio_player and self.player:
            if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.audio_player.play()
            else:
                self.audio_player.pause()

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
        elif item.kind == "image":
            if self._preview_media_id != item.id:
                self.show_image(item.path)
                self._preview_media_id = item.id
        elif item.kind == "title":
            if self._preview_media_id != item.id:
                self.show_title(item.meta, self.project.height)
                self._preview_media_id = item.id
        else:
            if self._preview_media_id != item.id:
                path = item.proxy_path if self.use_proxy and item.proxy_path else item.path
                self.show_media(path)
                self._preview_media_id = item.id
            self.seek_seconds(offset)
        if self.player:
            # A video clip whose audio was auto-split onto a companion clip
            # (Clip.strip_audio) must not ALSO play its own embedded audio
            # here - otherwise it plays twice, once from this primary
            # player and once from the second player mixing in the split
            # companion via _sync_audio_track below.
            self.audio_out.setMuted(bool(clip and clip.strip_audio))
        self._sync_audio_track(t)

    def _sync_audio_track(self, t: float) -> None:
        """Loads/seeks the secondary player to whatever audio-track clip
        covers time t, independent of whatever the video track is showing -
        the two clips are usually different media (e.g. a split video's own
        now-silent picture plus its companion audio)."""
        if not self.audio_player:
            return
        _clip, aitem, aoffset = self.project.resolve_audio_at(t)
        if aitem is None:
            self._preview_audio_media_id = None
            self.audio_player.pause()
            return
        if self._preview_audio_media_id != aitem.id:
            self.audio_player.setSource(QUrl.fromLocalFile(aitem.path))
            self._preview_audio_media_id = aitem.id
        self._audio_seek_seconds(aoffset)

    def _audio_seek_seconds(self, t: float) -> None:
        """Same deferred-seek fix as MonitorBase.seek_seconds (setSource()
        is async), duplicated for this second player rather than shared -
        keeps the already-tested primary-player code path untouched."""
        if not self.audio_player:
            return
        ready = (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia,
                QMediaPlayer.MediaStatus.EndOfMedia)
        if self.audio_player.mediaStatus() in ready:
            self.audio_player.setPosition(int(t * 1000))
            self._pending_audio_seek = None
        else:
            self._pending_audio_seek = t

    def _audio_media_status_changed(self, status) -> None:
        if self._pending_audio_seek is None:
            return
        ready = (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia,
                QMediaPlayer.MediaStatus.EndOfMedia)
        if status in ready:
            self.audio_player.setPosition(int(self._pending_audio_seek * 1000))
            self._pending_audio_seek = None
