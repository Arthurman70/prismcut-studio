"""Anthropic Claude chat. Docs: https://docs.claude.com - key: https://console.anthropic.com"""
from __future__ import annotations

from typing import Callable, Optional

from ..core import http
from ..core.http import ProviderError
from ..core.media import kind_of
from .base import Adapter, CancelFn, ChatMessage, DeltaFn
from .tools import MAX_TOOL_ITERATIONS, Tool, ToolCall, ToolResult


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

    def _payload_message(self, m: ChatMessage) -> dict:
        if m.role == "assistant" and m.tool_calls:
            blocks: list[dict] = []
            if m.text:
                blocks.append({"type": "text", "text": m.text})
            for tc in m.tool_calls:
                blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments})
            return {"role": "assistant", "content": blocks}
        if m.role == "tool":
            # Anthropic has no dedicated tool role - results go back as a
            # user-role message containing a tool_result block.
            return {"role": "user", "content": [{"type": "tool_result",
                                                 "tool_use_id": m.tool_call_id, "content": m.text}]}
        return {"role": m.role, "content": self._content_for(m)}

    def chat(self, model: str, messages: list[ChatMessage], system: str = "",
             temperature: float = 0.7, on_delta: Optional[DeltaFn] = None,
             should_cancel: Optional[CancelFn] = None,
             tools: Optional[list[Tool]] = None,
             on_tool_call: Optional[Callable[[ToolCall], ToolResult]] = None) -> str:
        body = {"model": model, "max_tokens": 4096, "temperature": temperature,
                "messages": [self._payload_message(m) for m in messages]}
        if system:
            body["system"] = system
        url = self.base_url() + "/v1/messages"

        if tools and on_tool_call:
            body["tools"] = [{"name": t.name, "description": t.description,
                              "input_schema": t.parameters} for t in tools]
            for _ in range(MAX_TOOL_ITERATIONS):
                if should_cancel and should_cancel():
                    return ""
                data = http.request_json("POST", url, headers=self._headers(), json_body=body)
                content = data.get("content", [])
                calls = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
                if not calls:
                    return "".join(p.get("text", "") for p in content if isinstance(p, dict))
                body["messages"].append({"role": "assistant", "content": content})
                result_blocks = []
                for c in calls:
                    result = on_tool_call(ToolCall(c["id"], c["name"], c.get("input") or {}))
                    result_blocks.append({"type": "tool_result", "tool_use_id": result.tool_call_id,
                                          "content": result.content, "is_error": result.is_error})
                body["messages"].append({"role": "user", "content": result_blocks})
            return ""  # exhausted MAX_TOOL_ITERATIONS without a final answer

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
