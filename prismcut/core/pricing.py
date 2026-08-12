"""Per-model AI API pricing, for the cost estimator (core/cost_estimator.py)
and the price hints shown in model pickers.

Bundled data (assets/pricing.json) ships with the app - same "data not
code, edit without a release" philosophy as models.json, since AI pricing
changes at least as often as model lineups. On top of that, this module
can refresh from a copy of the same file hosted in the project's GitHub
repo, so an already-installed copy can pick up a pricing update without
needing a new app release at all - same throttled/disclosed/toggleable
pattern as core/updater.py's update check (once/24h, a Help-menu toggle,
falls back silently to the bundled/cached copy if the fetch fails or the
user is offline).

Qt-free plain functions by design, same style as core/updater.py and
core/pipeline.py - a refresh is "just another job", submitted through
jobs.submit() by the UI layer exactly like every other network call in
this app. get_prices() itself is a fast, synchronous, offline-safe read
with no I/O beyond a local file on first call - safe to call from the GUI
thread.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from . import http, paths

REPO = "Arthurman70/prismcut-studio"
PRICING_RAW_URL = f"https://raw.githubusercontent.com/{REPO}/main/prismcut/assets/pricing.json"
BUNDLED_PATH = Path(__file__).resolve().parent.parent / "assets" / "pricing.json"
REFRESH_INTERVAL_SECS = 24 * 3600

_cache: Optional[dict] = None   # in-process memo: model key -> pricing dict


def load_bundled() -> dict:
    """The full pricing.json structure shipped with the app (version/
    updated/note/units/prices) - best-effort, never raises."""
    try:
        return json.loads(BUNDLED_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"prices": {}}


def _cache_path() -> Path:
    return paths.cache_dir() / "pricing_remote.json"


def _load_cached_remote() -> Optional[dict]:
    p = _cache_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def fetch_remote() -> Optional[dict]:
    """One blocking network call - always run this via jobs.submit(), never
    directly on the GUI thread. Returns the fetched {model_key: pricing}
    map on success (also updating the on-disk cache and this module's
    in-process memo so the NEXT get_prices() call sees it immediately, no
    restart needed) or None on any failure - never raises, so callers can
    silently keep using the bundled/previously-cached copy."""
    global _cache
    try:
        data = http.request_json("GET", PRICING_RAW_URL, timeout=15)
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("prices"), dict):
        return None
    try:
        _cache_path().parent.mkdir(parents=True, exist_ok=True)
        _cache_path().write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass
    _cache = data["prices"]
    return _cache


def should_refresh(settings) -> bool:
    """Mirrors the updater's own throttle: a Help-menu toggle (default on -
    unlike staying on an old app version, stale pricing serves no one) and
    a once-per-24h cadence tracked the same way."""
    if not settings.get_bool("pricing/auto_refresh", True):
        return False
    last = float(settings.get("pricing/last_refresh_ts", 0) or 0)
    return (time.time() - last) >= REFRESH_INTERVAL_SECS


def mark_refreshed(settings) -> None:
    settings.set("pricing/last_refresh_ts", time.time())


def get_prices() -> dict:
    """Model key ('provider::id', same string ModelSpec.key produces) ->
    pricing dict. The best data currently available: a previously
    fetched-and-cached remote copy if one exists, else the bundled copy
    shipped with the app. Purely local/synchronous - never fetches over
    the network itself (see fetch_remote/should_refresh for that)."""
    global _cache
    if _cache is not None:
        return _cache
    remote = _load_cached_remote()
    data = remote if remote else load_bundled()
    _cache = data.get("prices") or {}
    return _cache
