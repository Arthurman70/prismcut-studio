"""Model registry.

AI model IDs churn every few months, so PrismCut treats them as *data*, not
code: ``prismcut/assets/models.json`` ships sane defaults and a user overlay
(``models.user.json`` in the app data dir) can add, override or hide models
without touching Python. The Model Manager dialog edits the overlay in-app.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import paths

CAPS = ("chat", "image_generate", "image_edit", "video_generate", "image_to_video",
        "tts", "music", "sfx", "transcribe", "transcribe_segments", "lip_sync")

BUILTIN_PATH = Path(__file__).resolve().parent.parent / "assets" / "models.json"


@dataclass
class ProviderSpec:
    name: str
    label: str
    key_env: str = ""
    key_url: str = ""
    base_url: str = ""
    doc_url: str = ""
    notes: str = ""
    auth: str = "bearer"  # bearer | header | query (informational)


@dataclass
class ModelSpec:
    id: str
    provider: str
    label: str = ""
    caps: list = field(default_factory=list)
    params: list = field(default_factory=list)  # [{name,label,type,min,max,step,choices,default}]
    notes: str = ""
    user: bool = False

    @property
    def key(self) -> str:
        return f"{self.provider}::{self.id}"

    @property
    def display(self) -> str:
        return self.label or self.id

    def default_params(self) -> dict:
        out = {}
        for p in self.params:
            if "default" in p:
                out[p["name"]] = p["default"]
        return out


class Registry:
    def __init__(self, builtin_path: Optional[Path] = None, user_path: Optional[Path] = None):
        self.builtin_path = Path(builtin_path or BUILTIN_PATH)
        self.user_path = Path(user_path) if user_path else paths.user_registry_path()
        self.providers: dict[str, ProviderSpec] = {}
        self.models: list[ModelSpec] = []
        self.hidden: set[str] = set()
        self.reload()

    # ------------------------------------------------------------------ load
    def reload(self) -> None:
        data = json.loads(self.builtin_path.read_text(encoding="utf-8"))
        self.providers = {}
        for name, p in data.get("providers", {}).items():
            self.providers[name] = ProviderSpec(name=name, **{k: v for k, v in p.items()
                                                              if k in ("label", "key_env", "key_url",
                                                                       "base_url", "doc_url", "notes", "auth")})
        self.models = [self._mk_model(m) for m in data.get("models", [])]
        self.hidden = set()
        if self.user_path.exists():
            try:
                user = json.loads(self.user_path.read_text(encoding="utf-8"))
            except Exception:
                user = {}
            self.hidden = set(user.get("hidden", []))
            for m in user.get("models", []):
                spec = self._mk_model(m, user=True)
                existing = self.find(spec.provider, spec.id)
                if existing:
                    self.models[self.models.index(existing)] = spec
                else:
                    self.models.append(spec)

    @staticmethod
    def _mk_model(m: dict, user: bool = False) -> ModelSpec:
        return ModelSpec(
            id=m["id"], provider=m["provider"], label=m.get("label", ""),
            caps=list(m.get("caps", [])), params=list(m.get("params", [])),
            notes=m.get("notes", ""), user=user,
        )

    # ----------------------------------------------------------------- query
    def provider(self, name: str) -> Optional[ProviderSpec]:
        return self.providers.get(name)

    def find(self, provider: str, model_id: str) -> Optional[ModelSpec]:
        for m in self.models:
            if m.provider == provider and m.id == model_id:
                return m
        return None

    def by_key(self, key: str) -> Optional[ModelSpec]:
        if "::" in key:
            prov, mid = key.split("::", 1)
            return self.find(prov, mid)
        for m in self.models:
            if m.id == key:
                return m
        return None

    def models_with(self, *caps: str, include_hidden: bool = False) -> list[ModelSpec]:
        out = []
        for m in self.models:
            if not include_hidden and m.key in self.hidden:
                continue
            if all(c in m.caps for c in caps):
                out.append(m)
        return out

    def providers_with(self, cap: str) -> list[ProviderSpec]:
        names = {m.provider for m in self.models_with(cap)}
        return [self.providers[n] for n in sorted(names) if n in self.providers]

    # ------------------------------------------------------------- user edit
    def _user_data(self) -> dict:
        if self.user_path.exists():
            try:
                return json.loads(self.user_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"models": [], "hidden": []}

    def save_user_model(self, spec_dict: dict) -> None:
        data = self._user_data()
        models = [m for m in data.get("models", [])
                  if not (m["id"] == spec_dict["id"] and m["provider"] == spec_dict["provider"])]
        models.append(spec_dict)
        data["models"] = models
        self.user_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self.reload()

    def remove_user_model(self, provider: str, model_id: str) -> None:
        data = self._user_data()
        data["models"] = [m for m in data.get("models", [])
                          if not (m["id"] == model_id and m["provider"] == provider)]
        self.user_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self.reload()

    def set_hidden(self, key: str, hidden: bool) -> None:
        data = self._user_data()
        h = set(data.get("hidden", []))
        (h.add if hidden else h.discard)(key)
        data["hidden"] = sorted(h)
        self.user_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self.reload()

    def export_json(self, path: Path) -> None:
        Path(path).write_text(json.dumps({
            "providers": {n: vars(p) for n, p in self.providers.items()},
            "models": [{"id": m.id, "provider": m.provider, "label": m.label,
                        "caps": m.caps, "params": m.params, "notes": m.notes} for m in self.models],
        }, indent=2), encoding="utf-8")
