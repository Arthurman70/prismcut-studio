"""Lightweight spell checking (Qt-free) backed by pyspellchecker - pure
Python with no system/C dependency (bundles its own frequency dictionary
as package data), unlike pyenchant/hunspell which need an external
library plus dictionary files. Soft-fails to empty results if the import
itself ever fails: spell check is a non-blocking enhancement, never a
hard requirement of using a text field."""
from __future__ import annotations

import re

_WORD_RE = re.compile(r"[A-Za-z']+")

_checker = None
_unavailable = False


def _get_checker():
    global _checker, _unavailable
    if _unavailable:
        return None
    if _checker is None:
        try:
            from spellchecker import SpellChecker
            _checker = SpellChecker()
        except Exception:
            _unavailable = True
            return None
    return _checker


def misspelled_spans(text: str) -> list[tuple[int, int]]:
    """Character (start, end) spans of words the dictionary doesn't
    recognize, in text order - ready to feed straight to a highlighter.
    Tokenizes on letters+apostrophes so trailing/leading punctuation
    never gets glued onto a word (which would falsely flag it), while
    contractions like "don't"/"it's" stay intact. Empty if the library
    isn't available or nothing looks misspelled."""
    checker = _get_checker()
    if checker is None or not text:
        return []
    tokens = list(_WORD_RE.finditer(text))
    if not tokens:
        return []
    unknown = checker.unknown(m.group() for m in tokens)
    if not unknown:
        return []
    # unknown() lowercases internally, so match back to the original
    # tokens case-insensitively rather than relying on exact membership.
    unknown_lower = {w.lower() for w in unknown}
    return [m.span() for m in tokens if m.group().lower() in unknown_lower]


def suggestions(word: str, limit: int = 5) -> list[str]:
    """Up to `limit` candidate corrections for a single word - the
    dictionary's own best guess first (if any), then its other
    candidates. Empty if the library isn't available."""
    checker = _get_checker()
    if checker is None or not word:
        return []
    best = checker.correction(word)
    ordered = [best] if best else []
    for c in sorted(checker.candidates(word) or ()):
        if c not in ordered:
            ordered.append(c)
        if len(ordered) >= limit:
            break
    return ordered[:limit]
