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
from ...core.project import Bin, MediaItem, Project
from ...core.undo_commands import AddOrRemoveItemCommand, ChangePropertiesCommand
from ..widgets.common import DropAcceptor

GROUPS = [("import", "📁 Media"), ("generated", "✨ AI Generated"), ("audio", "🎵 Audio"),
          ("titles", "🔤 Titles")]

# Fixed palette (name, hex, display square) - a MediaItem.label_color that
# doesn't match one of these (e.g. from a future version's wider palette)
# just shows with no square rather than erroring.
LABEL_COLORS = [
    ("Red", "#ef5350", "🟥"), ("Orange", "#ffa726", "🟧"), ("Yellow", "#ffee58", "🟨"),
    ("Green", "#66bb6a", "🟩"), ("Blue", "#42a5f5", "🟦"), ("Purple", "#ab47bc", "🟪"),
]
# A second data role (UserRole is already the leaf media_id) marking a
# top-level tree item as a bin header rather than a kind-based group, so
# the context menu handler can tell them apart with a single lookup.
_BIN_HEADER_ROLE = Qt.ItemDataRole.UserRole + 1


class ProjectBin(QWidget):
    mediaActivated = Signal(str)          # media_id -> show in clip monitor
    addToTimeline = Signal(str)           # media_id
    openInPhotoStudio = Signal(str)       # media_id (images)
    sendToChat = Signal(str)              # media_id
    useAsReference = Signal(str)          # media_id -> generate panel reference
    transcribeRequested = Signal(str)     # media_id (audio/video)
    captionsRequested = Signal(str)       # media_id (audio/video) -> timestamped SRT captions
    proxyRequested = Signal(str)          # media_id (video) -> generate a low-res preview proxy
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

        self.offline_banner = QPushButton("")
        self.offline_banner.setStyleSheet(
            "text-align:left; padding:6px; font-weight:bold; background:#4a2f1a; "
            "color:#ffb74d; border:1px solid #ffb74d; border-radius:3px;")
        self.offline_banner.setToolTip("Click to browse a folder and auto-relink by filename")
        self.offline_banner.clicked.connect(self._relink_all_dialog)
        self.offline_banner.hide()
        lay.addWidget(self.offline_banner)

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
        offline_count = sum(1 for m in self.project.media.values() if m.offline)
        if offline_count:
            plural = "s" if offline_count != 1 else ""
            self.offline_banner.setText(f"⚠️ {offline_count} file{plural} missing — click to relink…")
            self.offline_banner.show()
        else:
            self.offline_banner.hide()
        if not self.project.media:
            empty = QTreeWidgetItem(["📥  Drag files here, or click “＋ Import media” to get started"])
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.tree.addTopLevelItem(empty)
            return
        bin_roots = {}
        for b in self.project.bins:
            root = QTreeWidgetItem([f"📂 {b.name}"])
            root.setFlags(root.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            root.setData(0, _BIN_HEADER_ROLE, b.id)
            bin_roots[b.id] = root
            self.tree.addTopLevelItem(root)
        roots = {}
        for key, title in GROUPS:
            root = QTreeWidgetItem([title])
            root.setFlags(root.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            roots[key] = root
            self.tree.addTopLevelItem(root)
        for item in sorted(self.project.media.values(), key=lambda m: m.label.lower()):
            color_sq = next((sq for _n, hexval, sq in LABEL_COLORS if hexval == item.label_color), "")
            prefix = ("⚠️ " if item.offline else "") + (f"{color_sq} " if color_sq else "")
            node = QTreeWidgetItem([prefix + self._label_for(item)])
            node.setData(0, Qt.ItemDataRole.UserRole, item.id)
            thumb = None if item.kind == "title" or item.offline else media_utils.thumbnail(item.path)
            if thumb:
                node.setIcon(0, QIcon(QPixmap(str(thumb))))
            else:
                kind_prefix = {"image": "🖼 ", "video": "🎬 ", "audio": "🎵 ", "title": "🔤 "} \
                    .get(item.kind, "📄 ")
                node.setText(0, prefix + kind_prefix + self._label_for(item))
            if item.offline:
                node.setToolTip(0, f"⚠️ File not found:\n{item.path}")
            elif item.kind == "title":
                node.setToolTip(0, item.meta.get("title_text", item.label))
            else:
                node.setToolTip(0, item.path + (f"\n{item.meta.get('prompt', '')}" if item.meta else ""))
            if item.bin_id in bin_roots:
                bin_roots[item.bin_id].addChild(node)
            else:
                group = item.group if item.group in roots else "import"
                if item.kind == "audio" and group == "import":
                    group = "audio"
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
        it = self.tree.itemAt(pos)
        if it is None:
            return
        bin_id = it.data(0, _BIN_HEADER_ROLE)
        if bin_id:
            self._bin_header_menu(bin_id, pos)
            return
        mid = self._selected_id()
        if not mid:
            return
        item = self.project.media.get(mid)
        if not item:
            return
        menu = QMenu(self)
        if item.offline:
            menu.addAction("🔗 Relink…", lambda: self._relink(item))
            menu.addSeparator()
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
        if item.kind == "video":
            menu.addAction("🎞 Regenerate proxy…" if item.proxy_path else "🎞 Generate proxy…",
                           lambda: self.proxyRequested.emit(mid))
        menu.addSeparator()
        color_menu = menu.addMenu("🎨 Color label")
        for name, hexval, sq in LABEL_COLORS:
            color_menu.addAction(f"{sq} {name}", lambda h=hexval: self._set_label_color(h))
        if item.label_color:
            color_menu.addSeparator()
            color_menu.addAction("✕ None", lambda: self._set_label_color(""))
        bin_menu = menu.addMenu("📂 Move to bin")
        for b in self.project.bins:
            bin_menu.addAction(b.name, lambda bid=b.id: self._set_bin(bid))
        if self.project.bins:
            bin_menu.addSeparator()
        bin_menu.addAction("➕ New bin…", self._new_bin_and_assign)
        if item.bin_id:
            bin_menu.addSeparator()
            bin_menu.addAction("✕ Remove from bin", lambda: self._set_bin(""))
        menu.addSeparator()
        menu.addAction("✎ Rename…", lambda: self._rename(item))
        if item.kind != "title":
            menu.addAction("📁 Reveal in Explorer", lambda: self._reveal(item))
        menu.addSeparator()
        n = len(self._selected_ids())
        menu.addAction(f"🗑 Remove {n} selected" if n > 1 else "🗑 Remove from project",
                       self._remove_selected)
        menu.exec(self.tree.mapToGlobal(pos))

    def _selected_items(self) -> list[MediaItem]:
        mids = self._selected_ids() or ([self._selected_id()] if self._selected_id() else [])
        return [self.project.media[m] for m in mids if m in self.project.media]

    def _set_label_color(self, color: str):
        items = self._selected_items()
        if not items:
            return
        text = "Set color label" if color else "Clear color label"
        self.undo_stack.beginMacro(text if len(items) == 1 else f"{text} ({len(items)} items)")
        for item in items:
            self.undo_stack.push(ChangePropertiesCommand(
                text, item, {"label_color": item.label_color}, {"label_color": color},
                self._undo_refresh))
        self.undo_stack.endMacro()

    def _set_bin(self, bin_id: str):
        items = self._selected_items()
        if not items:
            return
        text = "Move to bin" if bin_id else "Remove from bin"
        self.undo_stack.beginMacro(text if len(items) == 1 else f"{text} ({len(items)} items)")
        for item in items:
            self.undo_stack.push(ChangePropertiesCommand(
                text, item, {"bin_id": item.bin_id}, {"bin_id": bin_id}, self._undo_refresh))
        self.undo_stack.endMacro()

    def _new_bin_and_assign(self):
        from PySide6.QtWidgets import QInputDialog
        items = self._selected_items()
        text, ok = QInputDialog.getText(self, "New bin", "Name:")
        text = text.strip()
        if not ok or not text:
            return
        b = self.project.add_bin(text)
        self.undo_stack.beginMacro(f"New bin {b.name}")
        self.undo_stack.push(AddOrRemoveItemCommand(
            f"Add bin {b.name}", add=True,
            insert=lambda: self.project.bins.append(b) if b not in self.project.bins else None,
            remove=lambda: setattr(self.project, "bins", [x for x in self.project.bins if x.id != b.id]),
            refresh=self._undo_refresh))
        for item in items:
            self.undo_stack.push(ChangePropertiesCommand(
                "Move to bin", item, {"bin_id": item.bin_id}, {"bin_id": b.id}, self._undo_refresh))
        self.undo_stack.endMacro()

    def _bin_header_menu(self, bin_id: str, pos):
        b = next((x for x in self.project.bins if x.id == bin_id), None)
        if not b:
            return
        menu = QMenu(self)
        menu.addAction("✎ Rename bin…", lambda: self._rename_bin(b))
        menu.addAction("🗑 Delete bin", lambda: self._remove_bin(b))
        menu.exec(self.tree.mapToGlobal(pos))

    def _rename_bin(self, b: Bin):
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, "Rename bin", "Name:", text=b.name)
        text = text.strip()
        if ok and text and text != b.name:
            self.undo_stack.push(ChangePropertiesCommand(
                f"Rename bin to {text}", b, {"name": b.name}, {"name": text}, self._undo_refresh))

    def _remove_bin(self, b: Bin):
        # Ripples to every media item currently filed under it (un-filed
        # back to the default kind-based grouping, not deleted) so the
        # whole thing undoes/redoes as one step - same macro-of-commands
        # pattern _remove_selected uses for its clip cascade.
        affected = [m for m in self.project.media.values() if m.bin_id == b.id]
        self.undo_stack.beginMacro(f"Delete bin {b.name}")
        for m in affected:
            self.undo_stack.push(ChangePropertiesCommand(
                f"Un-file {m.label or 'media'}", m, {"bin_id": m.bin_id}, {"bin_id": ""},
                self._undo_refresh))
        self.undo_stack.push(AddOrRemoveItemCommand(
            f"Delete bin {b.name}", add=False,
            insert=lambda: self.project.bins.append(b) if b not in self.project.bins else None,
            remove=lambda: setattr(self.project, "bins", [x for x in self.project.bins if x.id != b.id]),
            refresh=self._undo_refresh))
        self.undo_stack.endMacro()

    def _apply_relink(self, item: MediaItem, new_path) -> None:
        """Shared by the single-item and folder-wide relink flows - always
        re-probes the replacement file rather than keeping the original's
        stale metadata, since the point of relinking is that this file is
        now the canonical source."""
        info = media_utils.probe(Path(new_path))
        before = {"path": item.path, "duration": item.duration, "width": item.width,
                 "height": item.height, "has_audio": item.has_audio, "offline": item.offline,
                 "proxy_path": item.proxy_path}
        # A proxy already generated from the OLD file's bytes would silently
        # keep being preferred for playback after relinking to a different
        # file - clear it so the "Generate proxy..." menu action offers a
        # fresh one instead of a stale mismatch. Undoing the relink restores
        # it along with everything else, via the same before/after diff.
        after = {"path": str(new_path), "duration": info["duration"], "width": info["width"],
                "height": info["height"], "has_audio": info["has_audio"], "offline": False,
                "proxy_path": ""}
        self.undo_stack.push(ChangePropertiesCommand(
            f"Relink {item.label or 'media'}", item, before, after, self._undo_refresh))

    def _relink(self, item: MediaItem):
        f, _ = QFileDialog.getOpenFileName(self, "Relink media", "", media_utils.MEDIA_FILTER)
        if f:
            self._apply_relink(item, Path(f))

    def _relink_all_dialog(self):
        folder = QFileDialog.getExistingDirectory(self, "Relink from folder")
        if not folder:
            return
        matches = self.project.find_relink_matches(folder)
        if not matches:
            self.status.emit("No matching filenames found in that folder.")
            return
        self.undo_stack.beginMacro(f"Relink {len(matches)} file(s)")
        for mid, path in matches.items():
            item = self.project.media.get(mid)
            if item:
                self._apply_relink(item, path)
        self.undo_stack.endMacro()
        self.status.emit(f"Relinked {len(matches)} file(s).")

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
