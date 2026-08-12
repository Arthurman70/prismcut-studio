"""App-wide undo/redo commands. Two generic classes cover every mutation
site (see docs/ or PR description for why) instead of one class per
operation - they manipulate Project's already-public dict/list attributes
(project.media / .tracks / .clips), the same ones Project's own methods use
internally, so this file needs zero changes to core/project.py."""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtGui import QUndoCommand


class ChangePropertiesCommand(QUndoCommand):
    """Generic setattr-diff on a single live object already referenced by
    the project (a Clip, Track, or MediaItem). Covers move/trim, mute
    toggle, effect param edits, and track/clip gain/pan/fades - one class,
    no per-field subclassing."""

    def __init__(self, text: str, obj: Any, before: dict, after: dict,
                 refresh: Callable[[], None], parent=None):
        super().__init__(text, parent)
        self._obj = obj
        self._before = dict(before)
        self._after = dict(after)
        self._refresh = refresh

    def redo(self):
        for k, v in self._after.items():
            setattr(self._obj, k, v)
        self._refresh()

    def undo(self):
        for k, v in self._before.items():
            setattr(self._obj, k, v)
        self._refresh()


class AddOrRemoveItemCommand(QUndoCommand):
    """Generic add/remove of a keyed item (clip, track, media). ``insert``
    and ``remove`` are small closures supplied by the call site that
    perform the actual dict/list mutation - this class just decides which
    one to call on redo vs undo, so it covers add-clip, delete-clip,
    duplicate, split's new-clip half, add-track, add/remove-media without
    any type-specific branching here."""

    def __init__(self, text: str, add: bool, insert: Callable[[], None],
                 remove: Callable[[], None], refresh: Callable[[], None], parent=None):
        super().__init__(text, parent)
        self._add = add
        self._insert = insert
        self._remove = remove
        self._refresh = refresh

    def redo(self):
        (self._insert if self._add else self._remove)()
        self._refresh()

    def undo(self):
        (self._remove if self._add else self._insert)()
        self._refresh()
