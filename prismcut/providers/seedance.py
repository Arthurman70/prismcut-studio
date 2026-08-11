"""ByteDance Seedance video via BytePlus ModelArk.

Docs: https://docs.byteplus.com/en/docs/ModelArk - key: BytePlus Ark console.
Seedance is also available through fal.ai/Replicate (see those providers) if
you prefer one aggregated key. Base URL is configurable for other regions.
"""
from __future__ import annotations

from typing import Optional

from ..core import http
from ..core.http import ProviderError
from .base import Adapter, CancelFn, ProgressFn


class Seedance(Adapter):
    name = "seedance"
    label = "Seedance (BytePlus Ark)"

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.key()}", "Content-Type": "application/json"}

    def generate_video(self, model: str, prompt: str, params: Optional[dict] = None,
                       image=None, progress: Optional[ProgressFn] = None,
                       should_cancel: Optional[CancelFn] = None) -> str:
        params = params or {}
        flags = []
        if params.get("resolution"):
            flags.append(f"--resolution {params['resolution']}")
        if params.get("duration"):
            flags.append(f"--duration {int(params['duration'])}")
        if params.get("aspect_ratio"):
            flags.append(f"--ratio {params['aspect_ratio']}")
        if params.get("seed") not in (None, -1, ""):
            flags.append(f"--seed {int(params['seed'])}")
        content: list[dict] = [{"type": "text", "text": (prompt + " " + " ".join(flags)).strip()}]
        if image is not None:
            content.append({"type": "image_url", "role": "first_frame",
                            "image_url": {"url": http.data_uri(image)}})
        body = {"model": model, "content": content}
        if "generate_audio" in params:
            body["generate_audio"] = bool(params["generate_audio"])
        data = http.request_json("POST", self.base_url() + "/contents/generations/tasks",
                                 headers=self._auth(), json_body=body, timeout=120)
        task_id = data.get("id") or data.get("task_id")
        if not task_id:
            raise ProviderError(f"Seedance did not return a task id: {str(data)[:300]}")

        def fetch():
            st = http.request_json("GET",
                                   f"{self.base_url()}/contents/generations/tasks/{task_id}",
                                   headers=self._auth())
            status = (st.get("status") or "").lower()
            if status in ("succeeded", "success", "completed"):
                return st
            if status in ("failed", "cancelled", "error"):
                err = st.get("error") or {}
                raise ProviderError(f"Seedance failed: {err.get('message', str(st)[:200])}")
            return None

        st = self._poll(fetch, interval=8, timeout=1800, progress=progress,
                        should_cancel=should_cancel, label="Seedance rendering")
        url = ((st.get("content") or {}).get("video_url")
               or next(iter(http.find_media_urls(st)), None))
        if not url:
            raise ProviderError(f"Seedance finished without video URL: {str(st)[:300]}")
        out = self._out("seedance", ".mp4")
        http.download(url, out)
        return str(out)
