"""xAI: Grok chat, Grok Imagine images & video.

Docs: https://docs.x.ai - key: https://console.x.ai
Video endpoint: POST /v1/videos/generations -> poll /v1/videos/{request_id}.
"""
from __future__ import annotations

import base64
from typing import Optional

from ..core import http
from ..core.http import ProviderError, NotSupported
from .base import CancelFn, ProgressFn
from .openai_compat import OpenAICompatChat


class XAI(OpenAICompatChat):
    name = "xai"
    label = "xAI (Grok)"

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.key()}", "Content-Type": "application/json"}

    def generate_image(self, model: str, prompt: str, params: Optional[dict] = None,
                       refs: Optional[list] = None) -> list[str]:
        if refs:
            if len(refs) != 1:
                raise NotSupported("Grok editing currently takes one reference image in PrismCut.")
            return self.edit_image(model, prompt, refs[0], params=params)
        params = params or {}
        body = {"model": model, "prompt": prompt, "n": int(params.get("n", 1)),
                "response_format": "b64_json"}
        for key in ("quality", "aspect_ratio", "resolution"):
            if params.get(key):
                body[key] = params[key]
        data = http.request_json("POST", self.base_url() + "/v1/images/generations",
                                 headers=self._auth(), json_body=body, timeout=300)
        return self._collect_images(data)

    def edit_image(self, model, prompt, image, mask=None, params=None):
        if mask is not None:
            raise NotSupported("Grok Image uses prompt-based whole-image edits. Clear the mask first.")
        params = params or {}
        body = {"model": model, "prompt": prompt, "n": int(params.get("n", 1)),
                "response_format": "b64_json", "image": {"type": "image_url", "url": http.data_uri(image)}}
        for key in ("quality", "aspect_ratio", "resolution"):
            if params.get(key):
                body[key] = params[key]
        data = http.request_json("POST", self.base_url() + "/v1/images/edits",
                                 headers=self._auth(), json_body=body, timeout=300)
        return self._collect_images(data)

    def _collect_images(self, data):
        outs = []
        for item in data.get("data", []):
            if item.get("b64_json"):
                raw = base64.b64decode(item["b64_json"])
                # Imagine 2 returns JPEG. Keep the real file type so later
                # attachments do not label JPEG bytes as image/png.
                ext = ".jpg" if raw.startswith(b"\xff\xd8\xff") else ".png"
                if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
                    ext = ".webp"
                outs.append(self._save_bytes(raw, "grok", ext))
            elif item.get("url"):
                ext = {"image/png": ".png", "image/webp": ".webp"}.get(item.get("mime_type"), ".jpg")
                out = self._out("grok", ext)
                http.download(item["url"], out)
                outs.append(str(out))
        if not outs:
            raise ProviderError(f"Grok returned no image: {str(data)[:300]}")
        return outs

    def generate_video(self, model: str, prompt: str, params: Optional[dict] = None,
                       image=None, progress: Optional[ProgressFn] = None,
                       should_cancel: Optional[CancelFn] = None) -> str:
        params = params or {}
        body: dict = {"model": model, "prompt": prompt}
        if params.get("duration"):
            body["duration"] = int(params["duration"])
        for k in ("aspect_ratio", "resolution"):
            if params.get(k):
                body[k] = params[k]
        if image is not None:
            body["image_url"] = http.data_uri(image)
        data = http.request_json("POST", self.base_url() + "/v1/videos/generations",
                                 headers=self._auth(), json_body=body, timeout=120)
        rid = data.get("request_id") or data.get("id")
        if not rid:
            urls = http.find_media_urls(data)
            if urls:
                out = self._out("grokvid", ".mp4")
                http.download(urls[0], out)
                return str(out)
            raise ProviderError(f"Grok Imagine did not return a request id: {str(data)[:300]}")

        def fetch():
            st = http.request_json("GET", f"{self.base_url()}/v1/videos/{rid}",
                                   headers=self._auth())
            status = (st.get("status") or "").lower()
            if status in ("done", "completed", "succeeded"):
                return st
            if status in ("failed", "expired", "cancelled"):
                raise ProviderError(f"Grok Imagine {status}: {str(st)[:200]}")
            return None

        st = self._poll(fetch, interval=5, timeout=900, progress=progress,
                        should_cancel=should_cancel, label="Grok Imagine")
        urls = http.find_media_urls(st)
        if not urls:
            raise ProviderError(f"Grok Imagine finished without a video URL: {str(st)[:300]}")
        out = self._out("grokvid", ".mp4")
        http.download(urls[0], out, headers=self._auth())
        return str(out)
