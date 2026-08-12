"""Shared chat implementation for every OpenAI-compatible endpoint
(OpenAI, xAI, DeepSeek, MiniMax, custom gateways...)."""
from __future__ import annotations

import json
from typing import Callable, Optional

from ..core import http
from ..core.http import ProviderError
from ..core.media import kind_of
from .base import Adapter, CancelFn, ChatMessage, DeltaFn
from .tools import MAX_TOOL_ITERATIONS, Tool, ToolCall, ToolResult


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

    def _payload_message(self, m: ChatMessage) -> dict:
        if m.role == "assistant" and m.tool_calls:
            return {"role": "assistant", "content": m.text or None,
                    "tool_calls": [{"id": tc.id, "type": "function",
                                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                                   for tc in m.tool_calls]}
        if m.role == "tool":
            return {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.text}
        return {"role": m.role, "content": self._content_for(m)}

    def chat(self, model: str, messages: list[ChatMessage], system: str = "",
             temperature: float = 0.7, on_delta: Optional[DeltaFn] = None,
             should_cancel: Optional[CancelFn] = None,
             tools: Optional[list[Tool]] = None,
             on_tool_call: Optional[Callable[[ToolCall], ToolResult]] = None) -> str:
        url = self.base_url() + self.chat_path
        payload_msgs = []
        if system:
            payload_msgs.append({"role": "system", "content": system})
        for m in messages:
            payload_msgs.append(self._payload_message(m))
        body = {"model": model, "messages": payload_msgs, "temperature": temperature}

        if tools and on_tool_call:
            body["tools"] = [{"type": "function",
                              "function": {"name": t.name, "description": t.description,
                                          "parameters": t.parameters}} for t in tools]
            body["tool_choice"] = "auto"
            for _ in range(MAX_TOOL_ITERATIONS):
                if should_cancel and should_cancel():
                    return ""
                data = http.request_json("POST", url, headers=self._chat_headers(), json_body=body)
                msg = data["choices"][0]["message"]
                raw_calls = msg.get("tool_calls") or []
                if not raw_calls:
                    text = msg.get("content") or ""
                    if isinstance(text, list):
                        text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
                    return text or ""
                payload_msgs.append({"role": "assistant", "content": msg.get("content"),
                                     "tool_calls": raw_calls})
                for rc in raw_calls:
                    try:
                        args = json.loads(rc["function"]["arguments"] or "{}")
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    result = on_tool_call(ToolCall(rc["id"], rc["function"]["name"], args))
                    payload_msgs.append({"role": "tool", "tool_call_id": result.tool_call_id,
                                         "content": result.content})
                body["messages"] = payload_msgs
            return ""  # exhausted MAX_TOOL_ITERATIONS without a final answer

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
