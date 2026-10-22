"""Responses transport for GPT-6, including streaming and native tool calls.

Reasoning models require this transport for tools. Keep encrypted reasoning
items during a tool loop while disabling server-side response storage.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..core import http, media
from ..core.http import ProviderError, NotSupported
from .tools import MAX_TOOL_ITERATIONS, ToolCall


def _input_items(messages):
    items = []
    for msg in messages:
        if msg.role == "tool":
            items.append({"type": "function_call_output", "call_id": msg.tool_call_id,
                          "output": msg.text})
            continue
        if msg.text or msg.attachments:
            parts = [{"type": "input_text" if msg.role != "assistant" else "output_text",
                      "text": msg.text or ""}]
            for path in msg.attachments:
                if media.kind_of(path) == "image":
                    parts.append({"type": "input_image", "image_url": http.data_uri(path)})
                elif Path(path).suffix.lower() == ".pdf":
                    parts.append({"type": "input_file", "filename": Path(path).name,
                                  "file_data": http.data_uri(path)})
                else:
                    raise NotSupported("GPT-6 chat accepts images and PDFs here. "
                                       "Use Audio Lab to transcribe audio first.")
            items.append({"role": msg.role, "content": parts})
        for call in msg.tool_calls:
            items.append({"type": "function_call", "call_id": call.id, "name": call.name,
                          "arguments": json.dumps(call.arguments)})
    return items


def _checked_output(data):
    if data.get("error") or data.get("status") in ("failed", "incomplete", "cancelled"):
        detail = data.get("error") or data.get("incomplete_details") or data.get("status")
        raise ProviderError(f"OpenAI response did not complete: {detail}")
    if not isinstance(data.get("output"), list):
        raise ProviderError("OpenAI returned an unexpected Responses API payload.")
    return data["output"]


def chat(adapter, model, messages, system="", temperature=0.7, on_delta=None,
         should_cancel=None, tools=None, on_tool_call=None):
    body = {"model": model, "input": _input_items(messages), "store": False,
            "reasoning": {"effort": "medium"}, "include": ["reasoning.encrypted_content"]}
    if system:
        body["instructions"] = system
    # No temperature with reasoning. Affects GPT-6 only; legacy models keep
    # the existing Chat Completions path and their sampling controls.
    url = adapter.base_url().removesuffix("/v1") + "/v1/responses"
    headers = adapter._chat_headers()
    if tools and on_tool_call:
        body["tools"] = [{"type": "function", "name": t.name,
                          "description": t.description, "parameters": t.parameters,
                          "strict": False} for t in tools]
        for _ in range(MAX_TOOL_ITERATIONS):
            if should_cancel and should_cancel():
                return ""
            data = http.request_json("POST", url, headers=headers, json_body=body, timeout=600)
            output = _checked_output(data)
            calls = [item for item in output if item.get("type") == "function_call"]
            if not calls:
                return _text(output)
            body["input"].extend(output)  # Includes opaque reasoning, unchanged.
            for call in calls:
                if should_cancel and should_cancel():
                    return ""
                try:
                    arguments = json.loads(call.get("arguments") or "{}")
                    if not isinstance(arguments, dict):
                        raise ValueError("arguments must be an object")
                except (ValueError, TypeError) as exc:
                    raise ProviderError("OpenAI returned invalid tool arguments.") from exc
                result = on_tool_call(ToolCall(call["call_id"], call["name"], arguments))
                body["input"].append({"type": "function_call_output",
                                      "call_id": call["call_id"], "output": result.content})
        raise ProviderError("OpenAI reached the tool-call limit before answering.")
    if should_cancel and should_cancel():
        return ""
    if on_delta:
        body["stream"] = True
        chunks, completed = [], False
        for event in http.stream_sse("POST", url, headers=headers, json_body=body, timeout=600):
            if should_cancel and should_cancel():
                return "".join(chunks)
            kind = event.get("type")
            if kind == "response.output_text.delta":
                text = event.get("delta", "")
                chunks.append(text)
                on_delta(text)
            elif kind == "response.completed":
                _checked_output(event.get("response", {}))
                completed = True
            elif kind in ("error", "response.failed", "response.incomplete"):
                raise ProviderError("OpenAI could not finish the response: " +
                                    str(event.get("message") or event.get("response", {}).get("error") or kind))
        if not completed:
            raise ProviderError("OpenAI's response stream ended before completion. Please retry.")
        return "".join(chunks)
    return _text(_checked_output(http.request_json("POST", url, headers=headers,
                                                  json_body=body, timeout=600)))


def _text(output):
    result = []
    for item in output:
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    result.append(part.get("text", ""))
                elif part.get("type") == "refusal":
                    result.append(part.get("refusal", ""))
    return "".join(result)
