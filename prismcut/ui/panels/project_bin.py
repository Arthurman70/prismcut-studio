"""Project Bin - the media library (Kdenlive-style), with thumbnails, groups,
drag-to-timeline and AI context actions."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QLineEdit,
                               QMenu, QPushButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from ...core import media as media_utils
from ...core.project import MediaItem, Project
from ...core.undo_commands import AddOrRemoveItemCommand, ChangePropertiesCommand
from ..widgets.common import DropAcceptor

GROUPS = [("import", "📁 Media"), ("generated", "✨ AI Generated"), ("audio", "🎵 Audio"),
          ("titles", "🔤 Titles")]


class ProjectBin(QWidget):
    mediaActivated = Signal(str)          # media_id -> show in clip monitor
    addToTimeline = Signal(str)           # media_id
    openInPhotoStudio = Signal(str)       # media_id (images)
    sendToChat = Signal(str)              # media_id
    useAsReference = Signal(str)          # media_id -> generate panel reference
    transcribeRequested = Signal(str)     # media_id (audio/video)
    captionsRequested = Signal(str)       # media_id (audio/video) -> timestamped SRT captions
    binChanged = Signal()
    status = Signal(str)

    def __init__(self, project: Project, undo_stack, parent=None):
        super().__init__(parent)
        self.project = project
        self.undo_stack = undo_stack
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)

        btns = QHBoxLayout()
        add = QPushButton("＋ Import media")
        add.clicked.connect(self.import_dialog)
        add_title = QPushButton("🔤 Title")
        add_title.setToolTip("Add a text/title clip")
        add_title.clicked.connect(self.add_title_dialog)
        rm = QPushButton("🗑")
        rm.setFixedWidth(34)
        rm.setToolTip("Remove selected from project")
        rm.setAccessibleName("Remove selected media from project")
        rm.clicked.connect(self._remove_selected)
        btns.addWidget(add, 1)
        btns.addWidget(add_title)
        btns.addWidget(rm)
        lay.addLayout(btns)

        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍 Filter media…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIconSize(QSize(64, 40))
        self.tree.setDragEnabled(False)
        # Not enabling internal drag-to-reorder here: QAbstractItemView's own
        # drop handling would compete with the OS-file-drop DropAcceptor
        # below for drop events landing on the tree, risking silently
        # breaking file import. Reordering would need a custom
        # dropMimeData() override to disambiguate the two - not attempted.
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        QShortcut(QKeySequence.StandardKey.SelectAll, self.tree,
                 activated=self._select_all_media, context=Qt.ShortcutContext.WidgetShortcut)
        QShortcut(QKeySequence.StandardKey.Delete, self.tree,
                 activated=self._remove_selected, context=Qt.ShortcutContext.WidgetShortcut)
        self.tree.itemDoubleClicked.connect(self._activate)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.tree, 1)
        DropAcceptor(self, ("image", "video", "audio"), self.add_files,
                    on_rejected=self._files_rejected)
        self.refresh()

    def _files_rejected(self, paths: list):
        names = ", ".join(Path(p).name for p in paths[:3])
        more = f" (+{len(paths) - 3} more)" if len(paths) > 3 else ""
        self.status.emit(f"Skipped unsupported file(s): {names}{more}")

    # ------------------------------------------------------------------ data
    def set_project(self, project: Project):
        self.project = project
        self.refresh()

    def refresh(self):
        self.tree.clear()
        if not self.project.media:
            empty = QTreeWidgetItem(["📥  Drag files here, or click “＋ Import media” to get started"])
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.tree.addTopLevelItem(empty)
            return
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
            thumb = None if item.kind == "title" else media_utils.thumbnail(item.path)
            if thumb:
                node.setIcon(0, QIcon(QPixmap(str(thumb))))
            else:
                node.setText(0, {"image": "🖼 ", "video": "🎬 ", "audio": "🎵 ", "title": "🔤 "}
                             .get(item.kind, "📄 ") + self._label_for(item))
            if item.kind == "title":
                node.setToolTip(0, item.meta.get("title_text", item.label))
            else:
                node.setToolTip(0, item.path + (f"\n{item.meta.get('prompt', '')}" if item.meta else ""))
            roots[group].addChild(node)
        self.tree.expandAll()
        self._filter(self.search.text())

    @staticmethod
    def _label_for(m: MediaItem) -> str:
        dur = f"  ·  {m.duration:.1f}s" if m.duration else ""
        return f"{m.label}{dur}"

    def _filter(self, text: str):
        text = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(i)
            any_visible = False
            for j in range(root.childCount()):
                child = root.child(j)
                match = not text or text in child.text(0).lower()
                child.setHidden(not match)
                any_visible = any_visible or match
            root.setHidden(not any_visible)

    # ------------------------------------------------------------------ import
    def import_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Import media", "", media_utils.MEDIA_FILTER)
        self.add_files(files)

    def add_files(self, files, group: str = "import", meta: dict | None = None):
        added = None
        rejected = []
        for f in files or []:
            p = Path(f)
            if not p.exists():
                continue
            if media_utils.kind_of(p) == "other":
                rejected.append(f)
                continue
            item = self.project.add_media(f, group=group, meta=meta)
            self.undo_stack.push(AddOrRemoveItemCommand(
                f"Add {item.label or 'media'}", add=True,
                insert=lambda it=item: self.project.media.__setitem__(it.id, it),
                remove=lambda it=item: self.project.media.pop(it.id, None),
                refresh=self._undo_refresh))
            added = item
        if rejected:
            self._files_rejected(rejected)
        return added

    def add_generated(self, path, meta: dict | None = None):
        kind = media_utils.kind_of(path)
        group = "generated" if kind != "audio" else "audio"
        item = self.project.add_media(path, group=group, meta=meta)
        self.undo_stack.push(AddOrRemoveItemCommand(
            f"Add {item.label or 'media'}", add=True,
            insert=lambda: self.project.media.__setitem__(item.id, item),
            remove=lambda: self.project.media.pop(item.id, None),
            refresh=self._undo_refresh))
        return item

    def add_title_dialog(self):
        from ..dialogs.title_dialog import TitleDialog
        dlg = TitleDialog(self.project, self)
        if dlg.exec():
            self.add_title_media(dlg.result_media())

    def add_title_media(self, item: MediaItem):
        self.undo_stack.push(AddOrRemoveItemCommand(
            f"Add {item.label or 'title'}", add=True,
            insert=lambda: self.project.media.__setitem__(item.id, item),
            remove=lambda: self.project.media.pop(item.id, None),
            refresh=self._undo_refresh))
        return item

    def _undo_refresh(self):
        self.project.dirty = True
        self.refresh()
        self.binChanged.emit()

    # ------------------------------------------------------------------ events
    def _selected_id(self) -> str | None:
        it = self.tree.currentItem()
        return it.data(0, Qt.ItemDataRole.UserRole) if it else None

    def _selected_ids(self) -> list[str]:
        ids = [it.data(0, Qt.ItemDataRole.UserRole) for it in self.tree.selectedItems()]
        return [i for i in ids if i]

    def _select_all_media(self):
        for i in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(i)
            for j in range(root.childCount()):
                root.child(j).setSelected(True)

    def _activate(self, item, _col):
        mid = item.data(0, Qt.ItemDataRole.UserRole)
        if mid:
            self.mediaActivated.emit(mid)

    def _remove_selected(self):
        mids = self._selected_ids() or ([self._selected_id()] if self._selected_id() else [])
        items = [self.project.media[m] for m in mids if m in self.project.media]
        if not items:
            return
        text = (f"Remove {items[0].label or 'media'}" if len(items) == 1
               else f"Remove {len(items)} media items")
        self.undo_stack.beginMacro(text)
        for item in items:
            # Project.remove_media() cascades to any clips referencing this
            # media - snapshot them so undo can restore both together as one
            # atomic action, matching what the user experiences as one delete.
            cascade_clips = [c for c in self.project.clips.values() if c.media_id == item.id]

            def remove(item=item, cascade_clips=cascade_clips):
                self.project.media.pop(item.id, None)
                for c in cascade_clips:
                    self.project.clips.pop(c.id, None)

            def insert(item=item, cascade_clips=cascade_clips):
                self.project.media[item.id] = item
                for c in cascade_clips:
                    self.project.clips[c.id] = c

            item_text = f"Remove {item.label or 'media'}"
            if cascade_clips:
                item_text += f" (+{len(cascade_clips)} clip{'s' if len(cascade_clips) != 1 else ''})"
            self.undo_stack.push(AddOrRemoveItemCommand(item_text, add=False, insert=insert,
                                                         remove=remove, refresh=self._undo_refresh))
        self.undo_stack.endMacro()

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
            menu.addAction("🎬 Generate captions (SRT)…", lambda: self.captionsRequested.emit(mid))
        menu.addSeparator()
        menu.addAction("✎ Rename…", lambda: self._rename(item))
        if item.kind != "title":
            menu.addAction("📁 Reveal in Explorer", lambda: self._reveal(item))
        menu.addSeparator()
        n = len(self._selected_ids())
        menu.addAction(f"🗑 Remove {n} selected" if n > 1 else "🗑 Remove from project",
                       self._remove_selected)
        menu.exec(self.tree.mapToGlobal(pos))

    def _rename(self, item: MediaItem):
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, "Rename", "Label:", text=item.label)
        text = text.strip()
        if ok and text and text != item.label:
            self.undo_stack.push(ChangePropertiesCommand(
                f"Rename to {text}", item, {"label": item.label}, {"label": text},
                self._undo_refresh))

    def _reveal(self, item: MediaItem):
        import subprocess
        try:
            subprocess.run(["explorer", "/select,", str(Path(item.path).resolve())], check=False)
        except OSError:
            self.status.emit(f"Couldn't open Explorer for {item.path}")
