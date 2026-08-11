"""Replicate - aggregator; run any hosted model by "owner/name" id.

Docs: https://replicate.com/docs - key: https://replicate.com/account/api-tokens
"""
from __future__ import annotations

from typing import Optional

from ..core import http
from ..core.http import ProviderError
from .base import Adapter, CancelFn, ProgressFn


class Replicate(Adapter):
    name = "replicate"
    label = "Replicate (aggregator)"

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.key()}", "Content-Type": "application/json",
                "Prefer": "wait=30"}

    def _run(self, model: str, input_payload: dict, progress: Optional[ProgressFn] = None,
             should_cancel: Optional[CancelFn] = None) -> dict:
        data = http.request_json("POST", f"{self.base_url()}/v1/models/{model}/predictions",
                                 headers=self._auth(), json_body={"input": input_payload},
                                 timeout=120)
        status = data.get("status")
        get_url = (data.get("urls") or {}).get("get")
        if status in ("succeeded",):
            return data
        if not get_url:
            raise ProviderError(f"Replicate did not return a poll URL: {str(data)[:300]}")

        def fetch():
            st = http.request_json("GET", get_url,
                                   headers={"Authorization": f"Bearer {self.key()}"})
            s = st.get("status")
            if s == "succeeded":
                return st
            if s in ("failed", "canceled"):
                raise ProviderError(f"Replicate {s}: {str(st.get('error'))[:200]}")
            return None

        return self._poll(fetch, interval=4, timeout=1800, progress=progress,
                          should_cancel=should_cancel, label=f"Replicate {model.split('/')[-1]}")

    def _payload(self, prompt: str, params: Optional[dict], image=None) -> dict:
        payload: dict = {"prompt": prompt}
        for k, v in (params or {}).items():
            if v not in (None, "", -1):
                payload[k] = v
        if image is not None:
            payload["image"] = http.data_uri(image)
        return payload

    def _grab(self, result: dict, stem: str, ext: str) -> list[str]:
        urls = http.find_media_urls(result.get("output") or result)
        if not urls:
            raise ProviderError(f"Replicate returned no media: {str(result)[:300]}")
        outs = []
        for u in urls[:4]:
            out = self._out(stem, ext)
            http.download(u, out)
            outs.append(str(out))
        return outs

    def generate_image(self, model: str, prompt: str, params: Optional[dict] = None,
                       refs: Optional[list] = None) -> list[str]:
        result = self._run(model, self._payload(prompt, params, refs[0] if refs else None))
        return self._grab(result, "rep_img", ".png")

    def generate_video(self, model: str, prompt: str, params: Optional[dict] = None,
                       image=None, progress: Optional[ProgressFn] = None,
                       should_cancel: Optional[CancelFn] = None) -> str:
        result = self._run(model, self._payload(prompt, params, image), progress, should_cancel)
        return self._grab(result, "rep_vid", ".mp4")[0]

    def music(self, model: str, prompt: str, params: Optional[dict] = None,
              progress: Optional[ProgressFn] = None,
              should_cancel: Optional[CancelFn] = None) -> str:
        result = self._run(model, self._payload(prompt, params), progress, should_cancel)
        return self._grab(result, "rep_music", ".mp3")[0]
