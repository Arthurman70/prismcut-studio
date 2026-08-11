"""Project Bin - the media library (Kdenlive-style), with thumbnails, groups,
drag-to-timeline and AI context actions."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QMenu, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ...core import media as media_utils
from ...core.project import MediaItem, Project

MEDIA_FILTER = ("Media files (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif "
                "*.mp4 *.mov *.mkv *.webm *.avi *.m4v "
                "*.mp3 *.wav *.m4a *.aac *.ogg *.flac);;All files (*)")

GROUPS = [("import", "📁 Media"), ("generated", "✨ AI Generated"), ("audio", "🎵 Audio")]


class ProjectBin(QWidget):
    mediaActivated = Signal(str)          # media_id -> show in clip monitor
    addToTimeline = Signal(str)           # media_id
    openInPhotoStudio = Signal(str)       # media_id (images)
    sendToChat = Signal(str)              # media_id
    useAsReference = Signal(str)          # media_id -> generate panel reference
    transcribeRequested = Signal(str)     # media_id (audio/video)
    binChanged = Signal()

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)

        btns = QHBoxLayout()
        add = QPushButton("＋ Import media")
        add.clicked.connect(self.import_dialog)
        rm = QPushButton("🗑")
        rm.setFixedWidth(34)
        rm.setToolTip("Remove selected from project")
        rm.clicked.connect(self._remove_selected)
        btns.addWidget(add, 1)
        btns.addWidget(rm)
        lay.addLayout(btns)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIconSize(QSize(64, 40))
        self.tree.setDragEnabled(False)
        self.tree.itemDoubleClicked.connect(self._activate)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.tree, 1)
        self.setAcceptDrops(True)
        self.refresh()

    # ------------------------------------------------------------------ data
    def set_project(self, project: Project):
        self.project = project
        self.refresh()

    def refresh(self):
        self.tree.clear()
        roots = {}
        for key, title in GROUPS:
            root = QTreeWidgetItem([title])
            root.setFlags(root.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            roots[key] = root
            self.tree.addTopLevelItem(root)
        for item in sorted(self.project.media.values(), key=lambda m: m.label.lower()):
            group = item.group if item.group in roots else "import"
            if item.kind == "audio" and group == "import":
                group = "audio"
            node = QTreeWidgetItem([self._label_for(item)])
            node.setData(0, Qt.ItemDataRole.UserRole, item.id)
            thumb = media_utils.thumbnail(item.path)
            if thumb:
                node.setIcon(0, QIcon(QPixmap(str(thumb))))
            else:
                node.setText(0, {"image": "🖼 ", "video": "🎬 ", "audio": "🎵 "}.get(item.kind, "📄 ")
                             + self._label_for(item))
            node.setToolTip(0, item.path + (f"\n{item.meta.get('prompt', '')}" if item.meta else ""))
            roots[group].addChild(node)
        self.tree.expandAll()

    @staticmethod
    def _label_for(m: MediaItem) -> str:
        dur = f"  ·  {m.duration:.1f}s" if m.duration else ""
        return f"{m.label}{dur}"

    # ------------------------------------------------------------------ import
    def import_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Import media", "", MEDIA_FILTER)
        self.add_files(files)

    def add_files(self, files, group: str = "import", meta: dict | None = None):
        added = None
        for f in files or []:
            if Path(f).exists():
                added = self.project.add_media(f, group=group, meta=meta)
        if added is not None:
            self.refresh()
            self.binChanged.emit()
        return added

    def add_generated(self, path, meta: dict | None = None):
        kind = media_utils.kind_of(path)
        group = "generated" if kind != "audio" else "audio"
        item = self.project.add_media(path, group=group, meta=meta)
        self.refresh()
        self.binChanged.emit()
        return item

    # ------------------------------------------------------------------ events
    def _selected_id(self) -> str | None:
        it = self.tree.currentItem()
        return it.data(0, Qt.ItemDataRole.UserRole) if it else None

    def _activate(self, item, _col):
        mid = item.data(0, Qt.ItemDataRole.UserRole)
        if mid:
            self.mediaActivated.emit(mid)

    def _remove_selected(self):
        mid = self._selected_id()
        if mid:
            self.project.remove_media(mid)
            self.refresh()
            self.binChanged.emit()

    def _menu(self, pos):
        mid = self._selected_id()
        if not mid:
            return
        item = self.project.media.get(mid)
        if not item:
            return
        menu = QMenu(self)
        menu.addAction("▶ Preview in monitor", lambda: self.mediaActivated.emit(mid))
        menu.addAction("➕ Add to timeline at playhead", lambda: self.addToTimeline.emit(mid))
        if item.kind == "image":
            menu.addAction("🎨 Open in Photo Studio", lambda: self.openInPhotoStudio.emit(mid))
            menu.addAction("🧬 Use as generation reference", lambda: self.useAsReference.emit(mid))
        if item.kind in ("image", "audio", "video"):
            menu.addAction("💬 Send to AI chat", lambda: self.sendToChat.emit(mid))
        if item.kind in ("audio", "video"):
            menu.addAction("📝 Transcribe (AI)", lambda: self.transcribeRequested.emit(mid))
        menu.addSeparator()
        menu.addAction("🗑 Remove from project", self._remove_selected)
        menu.exec(self.tree.mapToGlobal(pos))

    # drag & drop files straight into the bin
    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        files = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        self.add_files(files)
