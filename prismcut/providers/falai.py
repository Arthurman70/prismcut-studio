"""fal.ai - aggregator hosting hundreds of image/video/audio models (Seedance,
Kling, Hailuo, FLUX, WAN, LTX...). One key, any hosted model id.

Docs: https://docs.fal.ai - key: https://fal.ai/dashboard/keys
Queue API: POST https://queue.fal.run/<model> -> poll status_url -> response_url.
Any model id can be typed into the Model Manager; inputs use sensible defaults
(prompt / image_url) plus whatever params the registry entry declares.
"""
from __future__ import annotations

from typing import Optional

from ..core import http
from ..core.http import ProviderError
from .base import Adapter, CancelFn, ProgressFn

QUEUE = "https://queue.fal.run"


class FalAI(Adapter):
    name = "fal"
    label = "fal.ai (aggregator)"

    def _auth(self) -> dict:
        return {"Authorization": f"Key {self.key()}", "Content-Type": "application/json"}

    def _run(self, model: str, payload: dict, progress: Optional[ProgressFn] = None,
             should_cancel: Optional[CancelFn] = None) -> dict:
        data = http.request_json("POST", f"{QUEUE}/{model}", headers=self._auth(),
                                 json_body=payload, timeout=120)
        status_url = data.get("status_url")
        response_url = data.get("response_url")
        if not status_url:
            return data  # synchronous response

        def fetch():
            st = http.request_json("GET", status_url, headers=self._auth())
            s = (st.get("status") or "").upper()
            if s == "COMPLETED":
                return st
            if s in ("FAILED", "CANCELLED", "ERROR"):
                raise ProviderError(f"fal.ai job {s}: {str(st)[:200]}")
            return None

        self._poll(fetch, interval=4, timeout=1800, progress=progress,
                   should_cancel=should_cancel, label=f"fal.ai {model.split('/')[-1]}")
        return http.request_json("GET", response_url, headers=self._auth())

    def _payload(self, prompt: str, params: Optional[dict], image=None) -> dict:
        payload: dict = {}
        if prompt:
            # Omitted (not just empty-string) when there's no real prompt -
            # e.g. a lip-sync model call, which has nothing to do with text
            # generation and might reject an unexpected empty field.
            payload["prompt"] = prompt
        for k, v in (params or {}).items():
            if v in (None, "", -1):
                continue
            payload[k] = v
        if image is not None:
            # Most fal video/image models take the source image as
            # "image_url" - a few (Kling's image-to-video endpoints, which
            # also support a separate end_image_url for start/end-frame
            # interpolation) use "start_image_url" instead. The registry
            # entry declares whichever field that specific model expects
            # (empty-string default, same pattern lip-sync's video_url/
            # audio_url params use) - honor it instead of guessing wrong.
            field = "start_image_url" if params and "start_image_url" in params else "image_url"
            payload[field] = http.data_uri(image)
        return payload

    def _download_all(self, result: dict, stem: str, ext_hint: str) -> list[str]:
        urls = http.find_media_urls(result)
        if not urls:
            raise ProviderError(f"fal.ai returned no media URLs: {str(result)[:300]}")
        outs = []
        for u in urls[:4]:
            clean = u.split("?")[0].lower()
            ext = next((e for e in (".mp4", ".webm", ".png", ".jpg", ".jpeg", ".webp",
                                    ".gif", ".mp3", ".wav", ".m4a", ".flac") if clean.endswith(e)),
                       ext_hint)
            out = self._out(stem, ext)
            http.download(u, out)
            outs.append(str(out))
        return outs

    def generate_image(self, model: str, prompt: str, params: Optional[dict] = None,
                       refs: Optional[list] = None) -> list[str]:
        payload = self._payload(prompt, params, refs[0] if refs else None)
        return self._download_all(self._run(model, payload), "fal_img", ".png")

    def edit_image(self, model: str, prompt: str, image, mask=None,
                   params: Optional[dict] = None) -> list[str]:
        payload = self._payload(prompt, params, image)
        if mask is not None:
            payload["mask_url"] = http.data_uri(mask)
        return self._download_all(self._run(model, payload), "fal_edit", ".png")

    def generate_video(self, model: str, prompt: str, params: Optional[dict] = None,
                       image=None, progress: Optional[ProgressFn] = None,
                       should_cancel: Optional[CancelFn] = None) -> str:
        payload = self._payload(prompt, params, image)
        result = self._run(model, payload, progress, should_cancel)
        return self._download_all(result, "fal_vid", ".mp4")[0]

    def music(self, model: str, prompt: str, params: Optional[dict] = None,
              progress: Optional[ProgressFn] = None,
              should_cancel: Optional[CancelFn] = None) -> str:
        payload = self._payload(prompt, params)
        result = self._run(model, payload, progress, should_cancel)
        return self._download_all(result, "fal_music", ".mp3")[0]
