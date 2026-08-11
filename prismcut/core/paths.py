"""Application data directories (Qt-free so tests can import it)."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def app_data_dir() -> Path:
    """Per-user writable data dir, cross platform."""
    override = os.environ.get("PRISMCUT_DATA_DIR")
    if override:
        d = Path(override)
    elif sys.platform.startswith("win"):
        d = Path(os.environ.get("APPDATA", Path.home())) / "PrismCut"
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / "PrismCut"
    else:
        d = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "prismcut"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sub(name: str) -> Path:
    d = app_data_dir() / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def generated_dir() -> Path:
    return _sub("generated")


def cache_dir() -> Path:
    return _sub("cache")


def thumbs_dir() -> Path:
    return _sub("cache/thumbs")


def chats_dir() -> Path:
    return _sub("chats")


def logs_dir() -> Path:
    return _sub("logs")


def studio_dir() -> Path:
    """Photo Studio version history."""
    return _sub("photostudio")


def user_registry_path() -> Path:
    return app_data_dir() / "models.user.json"


def prompt_history_path() -> Path:
    return app_data_dir() / "prompt_history.json"


def unique_path(directory: Path, stem: str, ext: str) -> Path:
    """Timestamped collision-free output path, e.g. generated/veo_20260811_121500.mp4."""
    directory.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    safe = "".join(c for c in stem if c.isalnum() or c in "-_")[:40] or "out"
    p = directory / f"{safe}_{ts}{ext}"
    i = 1
    while p.exists():
        p = directory / f"{safe}_{ts}_{i}{ext}"
        i += 1
    return p
