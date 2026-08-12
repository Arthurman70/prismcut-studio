"""Single source of truth for every keyboard shortcut in the app. Menu
actions register themselves here the moment they're created (see
MainWindow._act's category param); a few widget-local QShortcuts do too via
bind(). The '?' cheat-sheet dialog reads this list to render itself, so it
cannot silently drift out of sync with what's actually bound."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut


@dataclass
class ShortcutInfo:
    category: str
    action: str
    keys: str


REGISTRY: list[ShortcutInfo] = []


def register(category: str, action: str, keys: str) -> None:
    REGISTRY.append(ShortcutInfo(category, action, keys))


def bind(widget, keys: str, callback, category: str, action: str,
        context=Qt.ShortcutContext.WindowShortcut) -> QShortcut:
    """Create a real QShortcut *and* register it for the cheat sheet in one
    call, for shortcuts that aren't a QAction (menu-bound shortcuts
    self-register via MainWindow._act instead)."""
    register(category, action, keys)
    return QShortcut(QKeySequence(keys), widget, activated=callback, context=context)


def by_category() -> dict[str, list[ShortcutInfo]]:
    out: dict[str, list[ShortcutInfo]] = {}
    for s in REGISTRY:
        out.setdefault(s.category, []).append(s)
    return out
