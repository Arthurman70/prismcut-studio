"""Anthropic Claude chat. Docs: https://docs.claude.com - key: https://console.anthropic.com"""
from __future__ import annotations

from typing import Optional

from ..core import http
from ..core.http import ProviderError
from ..core.media import kind_of
from .base import Adapter, CancelFn, ChatMessage, DeltaFn


class Anthropic(Adapter):
    name = "anthropic"
    label = "Anthropic Claude"

    def _headers(self) -> dict:
        return {"x-api-key": self.key(), "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"}

    def _content_for(self, m: ChatMessage):
        if not m.attachments:
            return m.text
        parts: list[dict] = []
        for a in m.attachments:
            if kind_of(a) == "image":
                parts.append({"type": "image",
                              "source": {"type": "base64", "media_type": http.guess_mime(a),
                                         "data": http.file_b64(a)}})
        parts.append({"type": "text", "text": m.text or "(see attachment)"})
        return parts

    def chat(self, model: str, messages: list[ChatMessage], system: str = "",
             temperature: float = 0.7, on_delta: Optional[DeltaFn] = None,
             should_cancel: Optional[CancelFn] = None) -> str:
        body = {"model": model, "max_tokens": 4096, "temperature": temperature,
                "messages": [{"role": m.role, "content": self._content_for(m)} for m in messages]}
        if system:
            body["system"] = system
        url = self.base_url() + "/v1/messages"
        if on_delta is not None:
            body["stream"] = True
            full = []
            for ev in http.stream_sse("POST", url, headers=self._headers(), json_body=body):
                if should_cancel and should_cancel():
                    break
                if ev.get("type") == "content_block_delta":
                    t = (ev.get("delta") or {}).get("text")
                    if t:
                        full.append(t)
                        on_delta(t)
                elif ev.get("type") == "error":
                    raise ProviderError(str(ev.get("error", {}).get("message", ev)))
            return "".join(full)
        data = http.request_json("POST", url, headers=self._headers(), json_body=body)
        parts = data.get("content", [])
        return "".join(p.get("text", "") for p in parts if isinstance(p, dict))

    def test_key(self) -> tuple[bool, str]:
        try:
            http.request_json("GET", self.base_url() + "/v1/models",
                              headers=self._headers(), timeout=20)
            return True, "Key OK"
        except ProviderError as e:
            return False, str(e)
