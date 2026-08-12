"""Settings + API key vault.

Keys are stored in the OS keyring when the optional ``keyring`` package is
available, otherwise in QSettings (base64-obfuscated - NOT encryption; see
README security note). Environment variables always win, so power users can
run entirely key-file-free: GEMINI_API_KEY, OPENAI_API_KEY, XAI_API_KEY, ...
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSettings

try:
    import keyring  # type: ignore
    _HAVE_KEYRING = True
except Exception:  # pragma: no cover
    keyring = None
    _HAVE_KEYRING = False

_OBF_PREFIX = "b64:"
_SERVICE = "PrismCutStudio"


class Settings:
    """Implements the `keychain` protocol adapters rely on:
    get(key, default), set(key, value), get_key(provider, env), set_key(provider, value)."""

    def __init__(self):
        # PRISMCUT_DATA_DIR (same override core.paths already honors) sandboxes
        # this to a per-process ini file instead of the real OS registry -
        # without it, tests silently read/write the developer's actual
        # PrismCut settings (remembered model defaults, etc.), which is both
        # a real-usage-pollutes-tests correctness bug and, conversely, a way
        # a test run could clobber someone's real saved preferences.
        override = os.environ.get("PRISMCUT_DATA_DIR")
        if override:
            self.q = QSettings(str(Path(override) / "settings.ini"), QSettings.Format.IniFormat)
        else:
            self.q = QSettings("PrismCut", "PrismCutStudio")

    # ------------------------------------------------------------- generic
    def get(self, key: str, default=None):
        v = self.q.value(key)
        return default if v is None else v

    def set(self, key: str, value) -> None:
        self.q.setValue(key, value)
        self.q.sync()

    # ------------------------------------------------------------- api keys
    def get_key(self, provider: str, env_var: str = "") -> Optional[str]:
        for var in filter(None, [env_var, f"{provider.upper()}_API_KEY"]):
            v = os.environ.get(var)
            if v:
                return v.strip()
        if _HAVE_KEYRING:
            try:
                v = keyring.get_password(_SERVICE, provider)
                if v:
                    return v
            except Exception:
                pass
        raw = self.q.value(f"keys/{provider}")
        if isinstance(raw, str) and raw.startswith(_OBF_PREFIX):
            try:
                return base64.b64decode(raw[len(_OBF_PREFIX):]).decode("utf-8")
            except Exception:
                return None
        return raw or None

    def set_key(self, provider: str, value: str) -> None:
        value = (value or "").strip()
        if _HAVE_KEYRING:
            try:
                if value:
                    keyring.set_password(_SERVICE, provider, value)
                else:
                    try:
                        keyring.delete_password(_SERVICE, provider)
                    except Exception:
                        pass
                self.q.remove(f"keys/{provider}")
                self.q.sync()
                return
            except Exception:
                pass
        if value:
            self.q.setValue(f"keys/{provider}",
                            _OBF_PREFIX + base64.b64encode(value.encode()).decode("ascii"))
        else:
            self.q.remove(f"keys/{provider}")
        self.q.sync()

    def has_key(self, provider: str, env_var: str = "") -> bool:
        return bool(self.get_key(provider, env_var))

    # ------------------------------------------------------------- defaults
    def default_model(self, role: str, fallback: str = "") -> str:
        return str(self.get(f"defaults/{role}", fallback) or fallback)

    def set_default_model(self, role: str, key: str) -> None:
        self.set(f"defaults/{role}", key)

    # ------------------------------------------------------- recent/MRU lists
    def recent(self, key: str, limit: int = 10) -> list[str]:
        raw = self.q.value(f"recent/{key}")
        items = list(raw) if isinstance(raw, list) else []
        return [str(i) for i in items][:limit]

    def add_recent(self, key: str, value: str, limit: int = 10) -> None:
        items = [i for i in self.recent(key, limit=1000) if i != value]
        items.insert(0, value)
        self.set(f"recent/{key}", items[:limit])

    def clear_recent(self, key: str) -> None:
        self.set(f"recent/{key}", [])
