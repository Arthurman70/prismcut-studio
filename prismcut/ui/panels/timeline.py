"""Multi-track timeline (Kdenlive-style): video + audio tracks, drag, trim,
razor, snapping, zoom, playhead scrubbing and a lightweight preview player."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QPainter, QPen)
from PySide6.QtWidgets import (QButtonGroup, QGraphicsItem, QGraphicsLineItem,
                               QGraphicsRectItem, QGraphicsScene, QGraphicsView,
                               QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea,
                               QSlider, QToolButton, QVBoxLayout, QWidget)

from ...core.project import Clip, Project
from .. import theme

TRACK_H = 52
AUDIO_H = 42
HEADER_W = 118
MIN_PPS, MAX_PPS = 4, 240


def _fmt(t: float) -> str:
    m, s = divmod(max(0.0, t), 60.0)
    return f"{int(m):02d}:{s:04.1f}"


class ClipItem(QGraphicsRectItem):
    EDGE = 7

    def __init__(self, clip: Clip, timeline: "TimelineWidget", kind: str):
        self.clip = clip
        self.timeline = timeline
        self.kind = kind
        h = (TRACK_H if kind == "video" else AUDIO_H) - 6
        super().__init__(0, 0, max(4.0, clip.duration * timeline.pps), h)
        self.setPos(clip.start * timeline.pps, timeline.track_y(clip.track_id) + 3)
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                      | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                      | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self._mode = "move"          # move | trim_l | trim_r
        self._press_scene = QPointF()
        self._orig = (clip.start, clip.duration, clip.in_point)

    # ------------------------------------------------------------------ paint
    def paint(self, p: QPainter, _opt, _widget=None):
        r = self.rect()
        base = QColor(theme.CLIP_VIDEO if self.kind == "video" else theme.CLIP_AUDIO)
        if self.clip.muted:
            base = base.darker(180)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(theme.ACCENT) if self.isSelected() else base.darker(140),
                      2 if self.isSelected() else 1))
        p.setBrush(QBrush(base))
        p.drawRoundedRect(r, 4, 4)
        # fades
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 40))
        if self.clip.fade_in > 0:
            w = min(r.width(), self.clip.fade_in * self.timeline.pps)
            p.drawPolygon([r.bottomLeft(), QPointF(r.left() + w, r.top()), r.topLeft()])
        if self.clip.fade_out > 0:
            w = min(r.width(), self.clip.fade_out * self.timeline.pps)
            p.drawPolygon([r.bottomRight(), QPointF(r.right() - w, r.top()), r.topRight()])
        # label
        p.setPen(QColor("#eaf2f8"))
        f = QFont()
        f.setPixelSize(10)
        p.setFont(f)
        name = self.clip.label or "clip"
        if self.clip.effects:
            name = "✦ " + name
        p.drawText(r.adjusted(6, 2, -4, -2), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                   name)
        p.setPen(QColor(230, 240, 245, 140))
        p.drawText(r.adjusted(6, 2, -6, -3),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                   f"{self.clip.duration:.1f}s")

    # ----------------------------------------------------------------- hover
    def hoverMoveEvent(self, ev):
        x = ev.pos().x()
        if x <= self.EDGE or x >= self.rect().width() - self.EDGE:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().hoverMoveEvent(ev)

    # ----------------------------------------------------------------- mouse
    def mousePressEvent(self, ev):
        if self.timeline.tool == "razor":
            t = (self.scenePos().x() + ev.pos().x()) / self.timeline.pps
            self.timeline.razor_at(self.clip.id, t)
            ev.accept()
            return
        x = ev.pos().x()
        self._press_scene = ev.scenePos()
        self._orig = (self.clip.start, self.clip.duration, self.clip.in_point)
        if x <= self.EDGE:
            self._mode = "trim_l"
        elif x >= self.rect().width() - self.EDGE:
            self._mode = "trim_r"
        else:
            self._mode = "move"
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._mode == "move":
            super().mouseMoveEvent(ev)  # handled by itemChange
            return
        dx = (ev.scenePos().x() - self._press_scene.x()) / self.timeline.pps
        start0, dur0, in0 = self._orig
        if self._mode == "trim_r":
            new_dur = max(0.2, dur0 + dx)
            new_dur = self.timeline.snap_duration(start0, new_dur)
            self.clip.duration = new_dur
        else:  # trim_l
            delta = min(max(dx, -start0), dur0 - 0.2)
            new_start = start0 + delta
            new_start = self.timeline.snap_time(new_start)
            delta = new_start - start0
            self.clip.start = new_start
            self.clip.duration = dur0 - delta
            self.clip.in_point = max(0.0, in0 + delta)
        self.prepareGeometryChange()
        self.setRect(0, 0, max(4.0, self.clip.duration * self.timeline.pps), self.rect().height())
        self.setPos(self.clip.start * self.timeline.pps, self.pos().y())
        ev.accept()

    def mouseReleaseEvent(self, ev):
        super().mouseReleaseEvent(ev)
        self.timeline.commit_item(self)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self._mode == "move":
            pos: QPointF = value
            t = self.timeline.snap_time(max(0.0, pos.x() / self.timeline.pps))
            y = self.timeline.nearest_track_y(pos.y(), self.kind)
            return QPointF(t * self.timeline.pps, y + 3)
        return super().itemChange(change, value)

    def contextMenuEvent(self, ev):
        menu = QMenu()
        menu.addAction("✂ Razor at playhead",
                       lambda: self.timeline.razor_at(self.clip.id, self.timeline.playhead_time))
        menu.addAction("⧉ Duplicate", lambda: self.timeline.duplicate_clip(self.clip.id))
        menu.addAction("🔊 Unmute" if self.clip.muted else "🔇 Mute",
                       lambda: self.timeline.toggle_mute(self.clip.id))
        menu.addAction("🎛 Effects…", lambda: self.timeline.effectsRequested.emit(self.clip.id))
        menu.addSeparator()
        menu.addAction("🗑 Delete", lambda: self.timeline.delete_clip(self.clip.id))
        menu.exec(ev.screenPos())
        ev.accept()


class TimelineView(QGraphicsView):
    def __init__(self, scene: QGraphicsScene, timeline: "TimelineWidget"):
        super().__init__(scene)
        self.timeline = timeline
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setBackgroundBrush(QColor(theme.BG))
        self.setDragMode(QGraphicsView.DragMode.NoDrag)

    def drawBackground(self, p: QPainter, rect: QRectF):
        super().drawBackground(p, rect)
        y = 0.0
        for tr in self.timeline.ordered_tracks():
            h = TRACK_H if tr.kind == "video" else AUDIO_H
            color = QColor("#23272b") if tr.kind == "video" else QColor("#212a26")
            p.fillRect(QRectF(rect.left(), y, rect.width(), h - 1), color)
            y += h
        # second gridlines
        pps = self.timeline.pps
        step = 1 if pps > 40 else (5 if pps > 12 else 15)
        t0 = int(rect.left() / pps / step) * step
        p.setPen(QPen(QColor(255, 255, 255, 14)))
        t = t0
        while t * pps < rect.right():
            x = t * pps
            p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            t += step

    def mousePressEvent(self, ev):
        item = self.itemAt(ev.position().toPoint())
        if item is None and ev.button() == Qt.MouseButton.LeftButton:
            t = self.mapToScene(ev.position().toPoint()).x() / self.timeline.pps
            self.timeline.set_playhead(max(0.0, t))
        super().mousePressEvent(ev)

    def wheelEvent(self, ev):
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.timeline.zoom_steps(1 if ev.angleDelta().y() > 0 else -1)
            ev.accept()
            return
        super().wheelEvent(ev)


class Ruler(QWidget):
    def __init__(self, timeline: "TimelineWidget"):
        super().__init__()
        self.timeline = timeline
        self.setFixedHeight(24)
        self._drag = False

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.PANEL))
        pps = self.timeline.pps
        offset = self.timeline.view.horizontalScrollBar().value()
        step = 1 if pps > 40 else (5 if pps > 12 else 15)
        p.setPen(QColor(theme.TEXT_DIM))
        f = QFont()
        f.setPixelSize(9)
        p.setFont(f)
        t = int(offset / pps / step) * step
        while t * pps - offset < self.width():
            x = int(t * pps - offset)
            p.drawLine(x, 14, x, 24)
            p.drawText(x + 3, 11, _fmt(float(t)))
            t += step
        # playhead marker
        x = int(self.timeline.playhead_time * pps - offset)
        p.setPen(QPen(QColor(theme.ORANGE), 2))
        p.drawLine(x, 0, x, 24)

    def _scrub(self, ev):
        offset = self.timeline.view.horizontalScrollBar().value()
        t = (ev.position().x() + offset) / self.timeline.pps
        self.timeline.set_playhead(max(0.0, t))

    def mousePressEvent(self, ev):
        self._drag = True
        self._scrub(ev)

    def mouseMoveEvent(self, ev):
        if self._drag:
            self._scrub(ev)

    def mouseReleaseEvent(self, _ev):
        self._drag = False


class TrackHeader(QWidget):
    def __init__(self, track, timeline: "TimelineWidget"):
        super().__init__()
        self.track = track
        h = TRACK_H if track.kind == "video" else AUDIO_H
        self.setFixedSize(HEADER_W, h - 1)
        self.setStyleSheet(f"background:{theme.PANEL};border-bottom:1px solid {theme.BORDER};")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 2, 4, 2)
        chip = QLabel("🎬" if track.kind == "video" else "🎵")
        name = QLabel(track.name)
        name.setStyleSheet("font-weight:bold;")
        lay.addWidget(chip)
        lay.addWidget(name, 1)
        for text, attr, tip in (("M", "mute", "Mute track"), ("S", "solo", "Solo track (audio)")):
            b = QToolButton(text=text, checkable=True)
            b.setChecked(getattr(track, attr))
            b.setFixedSize(22, 20)
            b.setToolTip(tip)
            b.toggled.connect(lambda on, a=attr: (setattr(track, a, on),
                                                  timeline.timelineChanged.emit(),
                                                  timeline.view.viewport().update()))
            lay.addWidget(b)


class TimelineWidget(QWidget):
    playheadMoved = Signal(float)
    selectionChanged = Signal(object)      # clip_id | None
    effectsRequested = Signal(str)         # clip_id
    timelineChanged = Signal()

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.pps = 26.0
        self.tool = "select"
        self.snap = True
        self.playhead_time = 0.0
        self._items: dict[str, ClipItem] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(2)

        # ---------------------------------------------------------- toolbar
        bar = QHBoxLayout()
        self.btn_select = QToolButton(text="⬚ Select", checkable=True, checked=True)
        self.btn_razor = QToolButton(text="✂ Razor", checkable=True)
        group = QButtonGroup(self)
        for b in (self.btn_select, self.btn_razor):
            group.addButton(b)
            bar.addWidget(b)
        self.btn_select.toggled.connect(lambda on: on and self._set_tool("select"))
        self.btn_razor.toggled.connect(lambda on: on and self._set_tool("razor"))
        self.btn_snap = QToolButton(text="🧲 Snap", checkable=True, checked=True)
        self.btn_snap.toggled.connect(lambda on: setattr(self, "snap", on))
        bar.addWidget(self.btn_snap)
        bar.addSpacing(10)
        self.btn_play = QToolButton(text="▶ Preview")
        self.btn_play.setToolTip("Scrub-preview the timeline in the Project Monitor")
        self.btn_play.clicked.connect(self.toggle_preview)
        bar.addWidget(self.btn_play)
        del_btn = QToolButton(text="🗑 Clip")
        del_btn.setToolTip("Delete selected clip (Del)")
        del_btn.clicked.connect(self.delete_selected)
        bar.addWidget(del_btn)
        bar.addSpacing(10)
        addv = QToolButton(text="＋V track")
        addv.clicked.connect(lambda: (self.project.add_track("video"), self.refresh(True)))
        adda = QToolButton(text="＋A track")
        adda.clicked.connect(lambda: (self.project.add_track("audio"), self.refresh(True)))
        bar.addWidget(addv)
        bar.addWidget(adda)
        bar.addStretch(1)
        self.time_label = QLabel("00:00.0")
        self.time_label.setStyleSheet(f"color:{theme.ORANGE};font-weight:bold;")
        bar.addWidget(self.time_label)
        bar.addSpacing(12)
        bar.addWidget(QLabel("🔍"))
        self.zoom = QSlider(Qt.Orientation.Horizontal)
        self.zoom.setRange(MIN_PPS, MAX_PPS)
        self.zoom.setValue(int(self.pps))
        self.zoom.setFixedWidth(120)
        self.zoom.valueChanged.connect(self._zoomed)
        bar.addWidget(self.zoom)
        outer.addLayout(bar)

        # ------------------------------------------------------ ruler + view
        head_row = QHBoxLayout()
        head_row.setSpacing(0)
        corner = QWidget()
        corner.setFixedSize(HEADER_W, 24)
        corner.setStyleSheet(f"background:{theme.PANEL};")
        self.scene = QGraphicsScene()
        self.view = TimelineView(self.scene, self)
        self.ruler = Ruler(self)
        head_row.addWidget(corner)
        head_row.addWidget(self.ruler, 1)
        outer.addLayout(head_row)

        body = QHBoxLayout()
        body.setSpacing(0)
        self.header_area = QScrollArea()
        self.header_area.setFixedWidth(HEADER_W)
        self.header_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.header_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.header_area.setFrameShape(self.header_area.Shape.NoFrame)
        self.header_host = QWidget()
        self.header_lay = QVBoxLayout(self.header_host)
        self.header_lay.setContentsMargins(0, 0, 0, 0)
        self.header_lay.setSpacing(0)
        self.header_area.setWidget(self.header_host)
        self.header_area.setWidgetResizable(True)
        body.addWidget(self.header_area)
        body.addWidget(self.view, 1)
        outer.addLayout(body, 1)

        self.view.horizontalScrollBar().valueChanged.connect(lambda _v: self.ruler.update())
        self.view.verticalScrollBar().valueChanged.connect(
            self.header_area.verticalScrollBar().setValue)
        self.scene.selectionChanged.connect(self._sel_changed)

        self.playhead = QGraphicsLineItem()
        self.playhead.setPen(QPen(QColor(theme.ORANGE), 2))
        self.playhead.setZValue(1000)
        self.scene.addItem(self.playhead)

        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(66)
        self._preview_timer.timeout.connect(self._preview_tick)

        self.setMinimumHeight(200)
        self.refresh(True)

    # ------------------------------------------------------------ geometry
    def ordered_tracks(self):
        return self.project.video_tracks() + self.project.audio_tracks()

    def track_y(self, track_id: str) -> float:
        y = 0.0
        for tr in self.ordered_tracks():
            if tr.id == track_id:
                return y
            y += TRACK_H if tr.kind == "video" else AUDIO_H
        return y

    def total_height(self) -> float:
        return sum(TRACK_H if t.kind == "video" else AUDIO_H for t in self.ordered_tracks())

    def nearest_track_y(self, y: float, kind: str) -> float:
        best, best_d = 3.0, 1e9
        for tr in self.ordered_tracks():
            if tr.kind != kind:
                continue
            ty = self.track_y(tr.id)
            d = abs(ty + 3 - y)
            if d < best_d:
                best, best_d = ty, d
        return best

    def track_at_y(self, y: float, kind: str):
        cur = 0.0
        candidates = []
        for tr in self.ordered_tracks():
            h = TRACK_H if tr.kind == "video" else AUDIO_H
            if tr.kind == kind:
                candidates.append((abs(cur + h / 2 - y), tr))
            cur += h
        return min(candidates, key=lambda c: c[0])[1] if candidates else None

    # ------------------------------------------------------------ snapping
    def _snap_points(self) -> list[float]:
        pts = [0.0, self.playhead_time]
        for c in self.project.clips.values():
            pts += [c.start, c.end]
        return pts

    def snap_time(self, t: float) -> float:
        if not self.snap:
            return max(0.0, t)
        thresh = 8.0 / self.pps
        best, best_d = t, thresh
        for p in self._snap_points() + [round(t)]:
            d = abs(p - t)
            if d < best_d:
                best, best_d = p, d
        return max(0.0, best)

    def snap_duration(self, start: float, dur: float) -> float:
        end = self.snap_time(start + dur)
        return max(0.2, end - start)

    # ------------------------------------------------------------ rebuild
    def set_project(self, project: Project):
        self.project = project
        self.playhead_time = 0.0
        self.refresh(True)

    def refresh(self, rebuild_headers: bool = False):
        for item in self._items.values():
            self.scene.removeItem(item)
        self._items.clear()
        if rebuild_headers:
            while self.header_lay.count():
                w = self.header_lay.takeAt(0).widget()
                if w:
                    w.deleteLater()
            for tr in self.ordered_tracks():
                self.header_lay.addWidget(TrackHeader(tr, self))
            self.header_lay.addStretch(1)
        dur = max(self.project.duration() + 60.0, 120.0)
        self.scene.setSceneRect(0, 0, dur * self.pps, self.total_height())
        for c in self.project.clips.values():
            tr = self.project.track(c.track_id)
            if not tr:
                continue
            item = ClipItem(c, self, tr.kind)
            self.scene.addItem(item)
            self._items[c.id] = item
        self._place_playhead()
        self.ruler.update()
        self.view.viewport().update()

    # ------------------------------------------------------------ operations
    def _set_tool(self, tool: str):
        self.tool = tool
        self.view.setCursor(Qt.CursorShape.CrossCursor if tool == "razor"
                            else Qt.CursorShape.ArrowCursor)

    def commit_item(self, item: ClipItem):
        c = item.clip
        c.start = max(0.0, item.pos().x() / self.pps)
        tr = self.track_at_y(item.pos().y(), item.kind)
        if tr:
            c.track_id = tr.id
        self.project.dirty = True
        self.refresh()
        self.timelineChanged.emit()

    def razor_at(self, clip_id: str, t: float):
        if self.project.split_clip(clip_id, t):
            self.refresh()
            self.timelineChanged.emit()

    def duplicate_clip(self, clip_id: str):
        c = self.project.clips.get(clip_id)
        if c:
            nc = self.project.add_clip(c.media_id, c.track_id, c.end, c.duration)
            if nc:
                nc.in_point, nc.gain_db, nc.effects = c.in_point, c.gain_db, dict(c.effects)
            self.refresh()
            self.timelineChanged.emit()

    def toggle_mute(self, clip_id: str):
        c = self.project.clips.get(clip_id)
        if c:
            c.muted = not c.muted
            self.refresh()
            self.timelineChanged.emit()

    def delete_clip(self, clip_id: str):
        self.project.remove_clip(clip_id)
        self.refresh()
        self.selectionChanged.emit(None)
        self.timelineChanged.emit()

    def delete_selected(self):
        for item in list(self.scene.selectedItems()):
            if isinstance(item, ClipItem):
                self.project.remove_clip(item.clip.id)
        self.refresh()
        self.selectionChanged.emit(None)
        self.timelineChanged.emit()

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
        elif ev.key() == Qt.Key.Key_Space:
            self.toggle_preview()
        else:
            super().keyPressEvent(ev)

    def add_media_at_playhead(self, media_id: str):
        item = self.project.media.get(media_id)
        if not item:
            return
        if item.kind == "audio":
            tracks = self.project.audio_tracks()
        else:
            tracks = list(reversed(self.project.video_tracks()))  # V1 first
        if not tracks:
            return
        self.project.add_clip(media_id, tracks[0].id, self.playhead_time)
        self.refresh()
        self.timelineChanged.emit()

    # ------------------------------------------------------------ selection
    def _sel_changed(self):
        sel = [i for i in self.scene.selectedItems() if isinstance(i, ClipItem)]
        self.selectionChanged.emit(sel[0].clip.id if sel else None)

    def selected_clip(self):
        sel = [i for i in self.scene.selectedItems() if isinstance(i, ClipItem)]
        return sel[0].clip if sel else None

    # ------------------------------------------------------------ playhead
    def set_playhead(self, t: float):
        self.playhead_time = max(0.0, t)
        self._place_playhead()
        self.time_label.setText(_fmt(self.playhead_time))
        self.ruler.update()
        self.playheadMoved.emit(self.playhead_time)

    def _place_playhead(self):
        x = self.playhead_time * self.pps
        self.playhead.setLine(x, 0, x, self.total_height())

    def _zoomed(self, v: int):
        self.pps = float(v)
        self.refresh()

    def zoom_steps(self, steps: int):
        self.zoom.setValue(int(min(MAX_PPS, max(MIN_PPS, self.pps + steps * 6))))

    # ------------------------------------------------------------ preview
    def toggle_preview(self):
        if self._preview_timer.isActive():
            self._preview_timer.stop()
            self.btn_play.setText("▶ Preview")
        else:
            if self.playhead_time >= self.project.duration():
                self.set_playhead(0.0)
            self._preview_timer.start()
            self.btn_play.setText("⏸ Pause")

    def _preview_tick(self):
        self.set_playhead(self.playhead_time + 0.066)
        if self.playhead_time >= self.project.duration():
            self.toggle_preview()
