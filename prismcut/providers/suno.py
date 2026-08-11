"""Suno music generation via API gateways.

Suno has no self-serve official public API as of mid-2026, so this adapter
speaks the de-facto gateway protocol used by sunoapi.org-style services
(POST /api/v1/generate -> poll record-info by taskId). Point `base_url` at the
gateway you use (Settings ▸ API Keys ▸ advanced) and drop in that gateway's
key. Clearly experimental - endpoints are configurable, not hardcoded truth.
For a fully supported path, use ElevenLabs Music or MiniMax Music instead.
"""
from __future__ import annotations

from typing import Optional

from ..core import http
from ..core.http import ProviderError
from .base import Adapter, CancelFn, ProgressFn


class Suno(Adapter):
    name = "suno"
    label = "Suno (community gateway)"

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.key()}", "Content-Type": "application/json"}

    def music(self, model: str, prompt: str, params: Optional[dict] = None,
              progress: Optional[ProgressFn] = None,
              should_cancel: Optional[CancelFn] = None) -> str:
        params = params or {}
        body = {"prompt": prompt, "model": model, "customMode": False,
                "instrumental": bool(params.get("instrumental", False)),
                "callBackUrl": ""}
        if params.get("lyrics"):
            body.update({"customMode": True, "prompt": params["lyrics"],
                         "style": prompt, "title": params.get("title", "PrismCut track")})
        data = http.request_json("POST", self.base_url() + "/api/v1/generate",
                                 headers=self._auth(), json_body=body, timeout=120)
        task_id = ((data.get("data") or {}).get("taskId")
                   or data.get("taskId") or data.get("task_id"))
        if not task_id:
            urls = http.find_media_urls(data)
            if urls:
                out = self._out("suno", ".mp3")
                http.download(urls[0], out)
                return str(out)
            raise ProviderError(f"Suno gateway did not return a task id: {str(data)[:300]}. "
                                "Check the gateway base URL in API Keys ▸ advanced.")

        def fetch():
            st = http.request_json("GET",
                                   self.base_url() + "/api/v1/generate/record-info",
                                   headers=self._auth(), params={"taskId": task_id})
            payload = st.get("data") or st
            status = str(payload.get("status") or "").upper()
            if status in ("SUCCESS", "COMPLETE", "COMPLETED", "TEXT_SUCCESS", "FIRST_SUCCESS"):
                urls = http.find_media_urls(payload)
                if urls:
                    return urls
                if status in ("SUCCESS", "COMPLETE", "COMPLETED"):
                    raise ProviderError(f"Suno reported success but no audio URL: {str(payload)[:300]}")
            if "FAIL" in status or "ERROR" in status:
                raise ProviderError(f"Suno generation failed: {str(payload)[:200]}")
            return None

        urls = self._poll(fetch, interval=8, timeout=900, progress=progress,
                          should_cancel=should_cancel, label="Suno composing")
        out = self._out("suno", ".mp3")
        http.download(urls[0], out)
        return str(out)
