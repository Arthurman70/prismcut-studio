"""Shared chat implementation for every OpenAI-compatible endpoint
(OpenAI, xAI, DeepSeek, MiniMax, custom gateways...)."""
from __future__ import annotations

from typing import Optional

from ..core import http
from ..core.http import ProviderError
from ..core.media import kind_of
from .base import Adapter, CancelFn, ChatMessage, DeltaFn


class OpenAICompatChat(Adapter):
    chat_path = "/v1/chat/completions"
    supports_images_in_chat = True
    supports_stream = True

    def _chat_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key()}", "Content-Type": "application/json"}

    def _content_for(self, msg: ChatMessage):
        if not msg.attachments:
            return msg.text
        parts: list[dict] = [{"type": "text", "text": msg.text or ""}]
        skipped = []
        for path in msg.attachments:
            kind = kind_of(path)
            if kind == "image" and self.supports_images_in_chat:
                parts.append({"type": "image_url",
                              "image_url": {"url": http.data_uri(path)}})
            elif kind == "audio":
                # OpenAI-style input_audio part; providers that ignore it will error clearly
                try:
                    ext = str(path).lower().rsplit(".", 1)[-1]
                    parts.append({"type": "input_audio",
                                  "input_audio": {"data": http.file_b64(path),
                                                  "format": "wav" if ext == "wav" else "mp3"}})
                except Exception:
                    skipped.append(path)
            else:
                skipped.append(path)
        if skipped:
            parts[0]["text"] += "\n[Note: attachment(s) not supported by this provider: " + \
                ", ".join(str(s) for s in skipped) + "]"
        return parts

    def chat(self, model: str, messages: list[ChatMessage], system: str = "",
             temperature: float = 0.7, on_delta: Optional[DeltaFn] = None,
             should_cancel: Optional[CancelFn] = None) -> str:
        url = self.base_url() + self.chat_path
        payload_msgs = []
        if system:
            payload_msgs.append({"role": "system", "content": system})
        for m in messages:
            payload_msgs.append({"role": m.role, "content": self._content_for(m)})
        body = {"model": model, "messages": payload_msgs, "temperature": temperature}

        if self.supports_stream and on_delta is not None:
            body["stream"] = True
            full = []
            for ev in http.stream_sse("POST", url, headers=self._chat_headers(), json_body=body):
                if should_cancel and should_cancel():
                    break
                for choice in ev.get("choices", []):
                    delta = (choice.get("delta") or {}).get("content")
                    if delta:
                        full.append(delta)
                        on_delta(delta)
            return "".join(full)

        data = http.request_json("POST", url, headers=self._chat_headers(), json_body=body)
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ProviderError(f"Unexpected chat response: {str(data)[:300]}")
        if isinstance(text, list):  # some providers return content parts
            text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
        if on_delta and text:
            on_delta(text)
        return text or ""

    def test_key(self) -> tuple[bool, str]:
        try:
            http.request_json("GET", self.base_url() + "/v1/models",
                              headers={"Authorization": f"Bearer {self.key()}"}, timeout=20)
            return True, "Key OK"
        except ProviderError as e:
            if e.status in (401, 403):
                return False, str(e)
            return True, f"Key stored (endpoint replied: {e})"
