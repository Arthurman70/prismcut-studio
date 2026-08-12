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
from ...core.undo_commands import AddOrRemoveItemCommand, ChangePropertiesCommand
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
        self._orig = (clip.start, clip.duration, clip.in_point, clip.track_id)

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
        self._orig = (self.clip.start, self.clip.duration, self.clip.in_point, self.clip.track_id)
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
        if self.kind == "video" and self.timeline.project.next_adjacent_clip(self.clip.id) is not None:
            if self.clip.transition_out > 0:
                menu.addAction("✕ Remove crossfade",
                               lambda: self.timeline.set_transition(self.clip.id, 0.0))
            else:
                menu.addAction("🔀 Add crossfade to next clip…",
                               lambda: self.timeline.add_transition_dialog(self.clip.id))
        media = self.timeline.project.media.get(self.clip.media_id)
        pipeline_id = (media.meta.get("pipeline_id") if media else "") or ""
        scene_id = (media.meta.get("scene_id") if media else "") or ""
        if pipeline_id and scene_id:
            menu.addSeparator()
            menu.addAction("🔄 Regenerate this scene",
                           lambda: self.timeline.regenerateSceneRequested.emit(pipeline_id, scene_id))
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
        # Rubber-band only engages when the press starts on empty scene
        # space (Qt gives movable/selectable items first refusal on their
        # own press events), so this is additive to the existing per-clip
        # drag/ctrl-click-multi-select and doesn't change either.
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)

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

    def contextMenuEvent(self, ev):
        if self.itemAt(ev.pos()) is not None:
            return  # ClipItem.contextMenuEvent already handled it
        menu = QMenu(self)
        menu.addAction("＋ Add video track", lambda: self.timeline._add_track("video"))
        menu.addAction("＋ Add audio track", lambda: self.timeline._add_track("audio"))
        menu.addSeparator()
        scene_pos = self.mapToScene(ev.pos())
        t = scene_pos.x() / self.timeline.pps
        menu.addAction(f"Move playhead here ({_fmt(max(0.0, t))})",
                       lambda: self.timeline.set_playhead(max(0.0, t)))
        track = self.timeline.track_at_position(scene_pos.y())
        if track is not None and self.timeline.project.gap_at(track.id, max(0.0, t)):
            menu.addAction("⇤ Close gap",
                           lambda: self.timeline.close_gap_after(track.id, max(0.0, t)))
        menu.exec(ev.globalPos())


MARKER_COLORS = [
    ("Amber", "#e8a33d"), ("Red", "#ef5350"), ("Green", "#66bb6a"),
    ("Blue", "#42a5f5"), ("Purple", "#ab47bc"),
]


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
        # marker flags - small downward triangles on the baseline, drawn
        # before the playhead so the playhead line stays on top at t=0
        for m in self.timeline.project.markers:
            x = m.time * pps - offset
            if -8 <= x <= self.width() + 8:
                self._paint_marker(p, x, m.color, m.label)
        # playhead marker
        x = int(self.timeline.playhead_time * pps - offset)
        p.setPen(QPen(QColor(theme.ORANGE), 2))
        p.drawLine(x, 0, x, 24)

    def _paint_marker(self, p: QPainter, x: float, color: str, label: str):
        col = QColor(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(col))
        p.drawPolygon([QPointF(x - 5, 0), QPointF(x + 5, 0), QPointF(x, 7)])
        if label:
            p.setPen(QColor(theme.TEXT))
            p.drawText(int(x) + 7, 11, label)

    def _scrub(self, ev):
        offset = self.timeline.view.horizontalScrollBar().value()
        t = (ev.position().x() + offset) / self.timeline.pps
        self.timeline.set_playhead(max(0.0, t))

    def _time_at(self, ev) -> float:
        # QContextMenuEvent (unlike QMouseEvent) has no position()/QPointF
        # API in Qt6 - only the older pos()/QPoint one, hence this is a
        # separate helper from _scrub() rather than a shared one.
        offset = self.timeline.view.horizontalScrollBar().value()
        return max(0.0, (ev.pos().x() + offset) / self.timeline.pps)

    def mousePressEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        self._drag = True
        self._scrub(ev)

    def mouseMoveEvent(self, ev):
        if self._drag:
            self._scrub(ev)

    def mouseReleaseEvent(self, _ev):
        self._drag = False

    def contextMenuEvent(self, ev):
        t = self._time_at(ev)
        marker = self.timeline.project.marker_near(t, tolerance=8.0 / self.timeline.pps)
        menu = QMenu(self)
        if marker:
            menu.addAction("✏ Rename marker", lambda: self._rename(marker.id))
            color_menu = menu.addMenu("🎨 Color")
            for name, hexval in MARKER_COLORS:
                color_menu.addAction(name, lambda h=hexval: self.timeline.recolor_marker(marker.id, h))
            menu.addSeparator()
            menu.addAction("🗑 Delete marker", lambda: self.timeline.remove_marker(marker.id))
        else:
            menu.addAction(f"➕ Add marker here ({_fmt(t)})", lambda: self.timeline.add_marker_at(t))
        menu.exec(ev.globalPos())

    def _rename(self, marker_id: str):
        marker = next((m for m in self.timeline.project.markers if m.id == marker_id), None)
        if not marker:
            return
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, "Rename marker", "Label:", text=marker.label)
        if ok:
            self.timeline.rename_marker(marker_id, text)


class TrackHeader(QWidget):
    def __init__(self, track, timeline: "TimelineWidget"):
        super().__init__()
        self.track = track
        h = TRACK_H if track.kind == "video" else AUDIO_H
        self.setFixedSize(HEADER_W, h - 1)
        self.setObjectName("timelineCorner")   # shares the same look/QSS rule as the ruler corner
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

            def toggle(on, a=attr):
                def refresh():
                    timeline.timelineChanged.emit()
                    timeline.view.viewport().update()
                # Was a raw setattr with no undo entry - the identical
                # action from the Audio Lab mixer (TrackStrip._toggle_attr)
                # was already correctly undoable, so this was silently
                # non-reversible only depending on which panel you clicked.
                timeline.undo_stack.push(ChangePropertiesCommand(
                    f"{'Mute' if a == 'mute' else 'Solo'} {track.name}",
                    track, {a: not on}, {a: on}, refresh))

            b.toggled.connect(toggle)
            lay.addWidget(b)


class TimelineWidget(QWidget):
    playheadMoved = Signal(float)
    selectionChanged = Signal(object)      # clip_id | None
    effectsRequested = Signal(str)         # clip_id
    timelineChanged = Signal()
    regenerateSceneRequested = Signal(str, str)   # pipeline_id, scene_id

    def __init__(self, project: Project, undo_stack, parent=None, on_add_track=None):
        super().__init__(parent)
        self.project = project
        self.undo_stack = undo_stack
        # MainWindow.add_track() is the one undoable track-creation
        # primitive (also used by Agent Mode's create_track tool) - wired
        # in here so the Timeline's own toolbar/context-menu "Add track"
        # controls go through it too instead of mutating the project
        # directly with no undo entry. Falls back to a non-undoable direct
        # add only if never wired (e.g. a test constructing this in
        # isolation), so the widget still works standalone.
        self.on_add_track = on_add_track
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
        addv.clicked.connect(lambda: self._add_track("video"))
        adda = QToolButton(text="＋A track")
        adda.clicked.connect(lambda: self._add_track("audio"))
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
        corner.setObjectName("timelineCorner")
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

    def track_at_position(self, y: float):
        """The single track whose row contains `y` - unlike
        nearest_track_y()/track_at_y(), this isn't filtered to a specific
        kind and isn't a "nearest" search, since callers (the empty-space
        context menu) need to know exactly which track was clicked."""
        cur = 0.0
        for tr in self.ordered_tracks():
            h = TRACK_H if tr.kind == "video" else AUDIO_H
            if cur <= y < cur + h:
                return tr
            cur += h
        return None

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
        pts += [m.time for m in self.project.markers]
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

    def _undo_refresh(self):
        self.project.dirty = True
        self.refresh()
        self.timelineChanged.emit()

    def commit_item(self, item: ClipItem):
        c = item.clip
        start0, dur0, in0, track0 = item._orig
        c.start = max(0.0, item.pos().x() / self.pps)
        tr = self.track_at_y(item.pos().y(), item.kind)
        if tr:
            c.track_id = tr.id
        before = {"start": start0, "duration": dur0, "in_point": in0, "track_id": track0}
        after = {"start": c.start, "duration": c.duration, "in_point": c.in_point,
                "track_id": c.track_id}
        if before != after:
            self.undo_stack.push(ChangePropertiesCommand(
                f"Move/trim {c.label or 'clip'}", c, before, after, self._undo_refresh))
        else:
            self._undo_refresh()

    def razor_at(self, clip_id: str, t: float):
        c = self.project.clips.get(clip_id)
        if not c:
            return
        before = {"duration": c.duration, "fade_out": c.fade_out}
        right = self.project.split_clip(clip_id, t)
        if not right:
            return
        after = {"duration": c.duration, "fade_out": c.fade_out}
        self.undo_stack.beginMacro(f"Razor {c.label or 'clip'}")
        self.undo_stack.push(ChangePropertiesCommand("Trim", c, before, after, self._undo_refresh))
        self.undo_stack.push(AddOrRemoveItemCommand(
            "Split", add=True,
            insert=lambda: self.project.clips.__setitem__(right.id, right),
            remove=lambda: self.project.clips.pop(right.id, None),
            refresh=self._undo_refresh))
        self.undo_stack.endMacro()

    def duplicate_clip(self, clip_id: str):
        c = self.project.clips.get(clip_id)
        if not c:
            return
        nc = self.project.add_clip(c.media_id, c.track_id, c.end, c.duration)
        if not nc:
            return
        nc.in_point, nc.gain_db, nc.effects = c.in_point, c.gain_db, dict(c.effects)
        self.undo_stack.push(AddOrRemoveItemCommand(
            f"Duplicate {c.label or 'clip'}", add=True,
            insert=lambda: self.project.clips.__setitem__(nc.id, nc),
            remove=lambda: self.project.clips.pop(nc.id, None),
            refresh=self._undo_refresh))

    def toggle_mute(self, clip_id: str):
        c = self.project.clips.get(clip_id)
        if not c:
            return
        before, after = {"muted": c.muted}, {"muted": not c.muted}
        self.undo_stack.push(ChangePropertiesCommand(
            f"{'Mute' if after['muted'] else 'Unmute'} {c.label or 'clip'}",
            c, before, after, self._undo_refresh))

    def delete_clip(self, clip_id: str):
        c = self.project.clips.get(clip_id)
        if not c:
            return
        self.undo_stack.push(AddOrRemoveItemCommand(
            f"Delete {c.label or 'clip'}", add=False,
            insert=lambda: self.project.clips.__setitem__(c.id, c),
            remove=lambda: self.project.clips.pop(c.id, None),
            refresh=self._undo_refresh))
        self.selectionChanged.emit(None)

    def delete_selected(self):
        clips = [item.clip for item in self.scene.selectedItems() if isinstance(item, ClipItem)]
        if not clips:
            return
        text = (f"Delete {clips[0].label or 'clip'}" if len(clips) == 1
               else f"Delete {len(clips)} clips")
        self.undo_stack.beginMacro(text)
        for c in clips:
            self.undo_stack.push(AddOrRemoveItemCommand(
                f"Delete {c.label or 'clip'}", add=False,
                insert=lambda c=c: self.project.clips.__setitem__(c.id, c),
                remove=lambda c=c: self.project.clips.pop(c.id, None),
                refresh=self._undo_refresh))
        self.undo_stack.endMacro()
        self.selectionChanged.emit(None)

    def keyPressEvent(self, ev):
        step = 1.0 if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0 / self.project.fps
        if ev.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
        elif ev.key() == Qt.Key.Key_Space:
            self.toggle_preview()
        elif ev.key() == Qt.Key.Key_Left:
            self.set_playhead(self.playhead_time - step)
        elif ev.key() == Qt.Key.Key_Right:
            self.set_playhead(self.playhead_time + step)
        elif ev.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal) and \
                ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_steps(1)
        elif ev.key() == Qt.Key.Key_Minus and ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_steps(-1)
        elif ev.key() == Qt.Key.Key_M:
            self.add_marker_at(self.playhead_time)
        else:
            super().keyPressEvent(ev)

    def _add_track(self, kind: str) -> None:
        if self.on_add_track:
            self.on_add_track(kind)
        else:
            self.project.add_track(kind)
            self.refresh(True)

    def add_marker_at(self, time: float, label: str = "") -> None:
        marker = self.project.add_marker(time, label)

        def insert(m=marker):
            if m not in self.project.markers:
                self.project.markers.append(m)
                self.project.markers.sort(key=lambda mk: mk.time)

        def remove(m=marker):
            self.project.markers = [mk for mk in self.project.markers if mk.id != m.id]

        def refresh():
            self.project.dirty = True
            self.ruler.update()

        self.undo_stack.push(AddOrRemoveItemCommand(
            "Add marker", add=True, insert=insert, remove=remove, refresh=refresh))

    def remove_marker(self, marker_id: str) -> None:
        marker = next((m for m in self.project.markers if m.id == marker_id), None)
        if not marker:
            return

        def insert(m=marker):
            if m not in self.project.markers:
                self.project.markers.append(m)
                self.project.markers.sort(key=lambda mk: mk.time)

        def remove(m=marker):
            self.project.markers = [mk for mk in self.project.markers if mk.id != m.id]

        def refresh():
            self.project.dirty = True
            self.ruler.update()

        self.undo_stack.push(AddOrRemoveItemCommand(
            "Delete marker", add=False, insert=insert, remove=remove, refresh=refresh))

    def rename_marker(self, marker_id: str, new_label: str) -> None:
        marker = next((m for m in self.project.markers if m.id == marker_id), None)
        if not marker:
            return
        before, after = {"label": marker.label}, {"label": new_label}

        def refresh():
            self.project.dirty = True
            self.ruler.update()

        self.undo_stack.push(ChangePropertiesCommand("Rename marker", marker, before, after, refresh))

    def recolor_marker(self, marker_id: str, new_color: str) -> None:
        marker = next((m for m in self.project.markers if m.id == marker_id), None)
        if not marker:
            return
        before, after = {"color": marker.color}, {"color": new_color}

        def refresh():
            self.project.dirty = True
            self.ruler.update()

        self.undo_stack.push(ChangePropertiesCommand("Recolor marker", marker, before, after, refresh))

    def close_gap_after(self, track_id: str, time: float) -> None:
        """Undoable wrapper around Project.gap_at()/close_gap_after() - a
        macro of one ChangePropertiesCommand per rippled clip so the whole
        ripple undoes/redoes as a single step, same pattern delete_selected
        uses for its own multi-clip macro."""
        found = self.project.gap_at(track_id, time)
        if not found:
            return
        gap, affected = found

        def refresh():
            self.project.dirty = True
            self.refresh(True)

        self.undo_stack.beginMacro("Close gap")
        for c in affected:
            before = {"start": c.start}
            after = {"start": max(0.0, c.start - gap)}
            self.undo_stack.push(ChangePropertiesCommand("Close gap", c, before, after, refresh))
        self.undo_stack.endMacro()

    def add_transition_dialog(self, clip_id: str) -> None:
        clip = self.project.clips.get(clip_id)
        nxt = self.project.next_adjacent_clip(clip_id)
        if not clip or not nxt:
            return
        max_overlap = max(0.1, min(clip.duration, nxt.duration) - 0.05)
        from PySide6.QtWidgets import QInputDialog
        value, ok = QInputDialog.getDouble(
            self, "Add crossfade", "Overlap duration (seconds):",
            min(0.5, max_overlap), 0.05, max_overlap, 2)
        if ok:
            self.set_transition(clip_id, value)

    def set_transition(self, clip_id: str, overlap: float) -> None:
        clip = self.project.clips.get(clip_id)
        if not clip:
            return
        before = {"transition_out": clip.transition_out}
        after = {"transition_out": max(0.0, overlap)}

        def refresh():
            self.project.dirty = True
            self.view.viewport().update()

        text = "Add crossfade" if overlap > 0 else "Remove crossfade"
        self.undo_stack.push(ChangePropertiesCommand(text, clip, before, after, refresh))

    def add_media_at_playhead(self, media_id: str, track_id: str = "",
                              start: float | None = None, duration: float | None = None,
                              label: str = ""):
        """track_id/start/duration are optional overrides for callers that
        know exactly where a clip belongs (the movie pipeline, Agent Mode's
        add_media_to_timeline tool) - the bare single-arg call (every
        existing caller) keeps its old behavior: first matching track,
        insert at the current playhead, default duration. label overrides
        the clip's displayed name (defaults to the media item's own label) -
        the movie pipeline uses this for "Scene N" tagging."""
        item = self.project.media.get(media_id)
        if not item:
            return None
        if track_id:
            tr = self.project.track(track_id)
            if not tr:
                return None
        else:
            tracks = (self.project.audio_tracks() if item.kind == "audio"
                     else list(reversed(self.project.video_tracks())))  # V1 first
            if not tracks:
                return None
            tr = tracks[0]
        t = self.playhead_time if start is None else start
        clip = self.project.add_clip(media_id, tr.id, t, duration, label)
        if not clip:
            return None
        # A video landing on the timeline gets its audio auto-split onto a
        # companion clip so each can be trimmed/muted/deleted independently -
        # bundled into the SAME undo step as adding the video itself, so one
        # Ctrl+Z removes both rather than leaving an orphaned audio clip.
        audio_clip = self.project.split_video_audio(clip)

        def insert():
            self.project.clips[clip.id] = clip
            if audio_clip:
                self.project.clips[audio_clip.id] = audio_clip

        def remove():
            self.project.clips.pop(clip.id, None)
            if audio_clip:
                self.project.clips.pop(audio_clip.id, None)

        self.undo_stack.push(AddOrRemoveItemCommand(
            f"Add {clip.label or 'media'} to timeline", add=True,
            insert=insert, remove=remove, refresh=self._undo_refresh))
        return clip

    # ------------------------------------------------------------ selection
    def _sel_changed(self):
        sel = [i for i in self.scene.selectedItems() if isinstance(i, ClipItem)]
        self.selectionChanged.emit(sel[0].clip.id if sel else None)

    def selected_clip(self):
        sel = [i for i in self.scene.selectedItems() if isinstance(i, ClipItem)]
        return sel[0].clip if sel else None

    def reveal_clip(self, clip_id: str) -> bool:
        """Seeks the playhead to this clip's start, scrolls it into view,
        and selects it - the "jump to" entry point for anything that names
        a clip by id rather than the user directly clicking the timeline
        (e.g. the movie pipeline panel's scene rows)."""
        c = self.project.clips.get(clip_id)
        if not c:
            return False
        self.set_playhead(c.start)
        self.view.centerOn(c.start * self.pps, self.track_y(c.track_id))
        item = self._items.get(clip_id)
        if item:
            self.scene.clearSelection()
            item.setSelected(True)
        return True

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
