"""Black Forest Labs FLUX: generation + Kontext in-context editing.

Docs: https://docs.bfl.ai - key: https://dashboard.bfl.ai
Async API: POST /v1/<model> -> poll polling_url until Ready -> result.sample URL.
"""
from __future__ import annotations

from typing import Optional

from ..core import http
from ..core.http import ProviderError
from .base import Adapter


class BFL(Adapter):
    name = "bfl"
    label = "Black Forest Labs (FLUX)"

    def _headers(self) -> dict:
        return {"x-key": self.key(), "Content-Type": "application/json"}

    def _run(self, model: str, body: dict, stem: str) -> str:
        data = http.request_json("POST", f"{self.base_url()}/v1/{model}",
                                 headers=self._headers(), json_body=body, timeout=120)
        poll_url = data.get("polling_url")
        rid = data.get("id")
        if not poll_url and not rid:
            raise ProviderError(f"FLUX did not return a polling url: {str(data)[:300]}")

        def fetch():
            st = http.request_json("GET", poll_url or f"{self.base_url()}/v1/get_result",
                                   headers=self._headers(),
                                   params=None if poll_url else {"id": rid})
            status = st.get("status")
            if status == "Ready":
                return st
            if status in ("Error", "Failed", "Content Moderated", "Request Moderated"):
                raise ProviderError(f"FLUX {status}: {str(st.get('details', ''))[:200]}")
            return None

        st = self._poll(fetch, interval=2, timeout=300, label="FLUX rendering")
        sample = (st.get("result") or {}).get("sample")
        if not sample:
            raise ProviderError(f"FLUX finished without a sample URL: {str(st)[:300]}")
        out = self._out(stem, ".png")
        http.download(sample, out)   # signed URL, valid ~10 min
        return str(out)

    def generate_image(self, model: str, prompt: str, params: Optional[dict] = None,
                       refs: Optional[list] = None) -> list[str]:
        params = params or {}
        body: dict = {"prompt": prompt}
        if params.get("aspect_ratio"):
            body["aspect_ratio"] = params["aspect_ratio"]
        for k in ("width", "height", "guidance", "steps"):
            if params.get(k) not in (None, "", 0):
                body[k] = params[k]
        if params.get("seed") not in (None, -1, ""):
            body["seed"] = int(params["seed"])
        if refs:
            body["input_image"] = http.file_b64(refs[0])
        return [self._run(model, body, "flux")]

    def edit_image(self, model: str, prompt: str, image, mask=None,
                   params: Optional[dict] = None) -> list[str]:
        params = params or {}
        body: dict = {"prompt": prompt, "input_image": http.file_b64(image)}
        if params.get("aspect_ratio"):
            body["aspect_ratio"] = params["aspect_ratio"]
        if params.get("seed") not in (None, -1, ""):
            body["seed"] = int(params["seed"])
        if mask is not None:  # flux-fill style models accept a mask
            body["mask"] = http.file_b64(mask)
        return [self._run(model, body, "kontext")]
