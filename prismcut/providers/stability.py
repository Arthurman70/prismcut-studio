"""Stability AI Stable Image services: generate, inpaint, background removal,
upscale - the "maximum-control" image provider (seed / cfg / style presets).

Docs: https://platform.stability.ai/docs - key: https://platform.stability.ai/account/keys
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..core import http
from ..core.http import ProviderError
from .base import Adapter


class Stability(Adapter):
    name = "stability"
    label = "Stability AI"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key()}", "Accept": "image/*"}

    def _post_image(self, path: str, fields: dict, files: Optional[dict] = None) -> str:
        files = files or {"none": ("", b"")}
        data = http.request_bytes("POST", self.base_url() + path,
                                  headers=self._headers(),
                                  data={k: str(v) for k, v in fields.items() if v not in (None, "")},
                                  files=files, timeout=300)
        return self._save_bytes(data, path.strip("/").split("/")[-1], ".png")

    def generate_image(self, model: str, prompt: str, params: Optional[dict] = None,
                       refs: Optional[list] = None) -> list[str]:
        params = params or {}
        endpoint = {"stable-image-ultra": "/v2beta/stable-image/generate/ultra",
                    "stable-image-core": "/v2beta/stable-image/generate/core",
                    "sd3.5-large": "/v2beta/stable-image/generate/sd3",
                    "sd3.5-medium": "/v2beta/stable-image/generate/sd3"}.get(
            model, "/v2beta/stable-image/generate/core")
        fields = {"prompt": prompt,
                  "negative_prompt": params.get("negative_prompt", ""),
                  "aspect_ratio": params.get("aspect_ratio", "1:1"),
                  "style_preset": params.get("style_preset", "")}
        if params.get("seed") not in (None, -1, ""):
            fields["seed"] = int(params["seed"])
        if endpoint.endswith("/sd3"):
            fields["model"] = model
            if params.get("cfg_scale"):
                fields["cfg_scale"] = params["cfg_scale"]
        files = {"none": ("", b"")}
        if refs:
            files = {"image": (Path(refs[0]).name, Path(refs[0]).read_bytes(),
                               http.guess_mime(refs[0]))}
            fields["strength"] = params.get("strength", 0.6)
            fields["mode"] = "image-to-image"
        return [self._post_image(endpoint, fields, files)]

    def edit_image(self, model: str, prompt: str, image, mask=None,
                   params: Optional[dict] = None) -> list[str]:
        params = params or {}
        img = Path(image)
        files = {"image": (img.name, img.read_bytes(), http.guess_mime(img))}
        if model == "remove-background":
            return [self._post_image("/v2beta/stable-image/edit/remove-background", {}, files)]
        if model == "upscale-fast":
            return [self._post_image("/v2beta/stable-image/upscale/fast", {}, files)]
        if model == "search-and-replace":
            fields = {"prompt": prompt, "search_prompt": params.get("search_prompt", "background")}
            return [self._post_image("/v2beta/stable-image/edit/search-and-replace", fields, files)]
        # default: inpaint (mask optional -> whole-image restyle)
        fields = {"prompt": prompt, "negative_prompt": params.get("negative_prompt", "")}
        if params.get("seed") not in (None, -1, ""):
            fields["seed"] = int(params["seed"])
        if mask is not None:
            files["mask"] = (Path(mask).name, Path(mask).read_bytes(), "image/png")
        return [self._post_image("/v2beta/stable-image/edit/inpaint", fields, files)]

    def test_key(self) -> tuple[bool, str]:
        try:
            http.request_json("GET", self.base_url() + "/v1/user/account",
                              headers={"Authorization": f"Bearer {self.key()}"}, timeout=20)
            return True, "Key OK"
        except ProviderError as e:
            return False, str(e)
