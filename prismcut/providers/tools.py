"""Provider-agnostic tool/function-calling representation.

Every chat-capable adapter that supports tool calling (Google, the
OpenAI-compatible family, Anthropic) translates these to/from its own wire
format - only the envelope differs per provider (where the schema sits in
the request, what the response shape looks like), never the schema content,
since the actual tool surface (core/agent.py) is flat scalars/enums that are
valid JSON-Schema for all three verbatim. Qt-free, like the rest of
providers/.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class ToolResult:
    tool_call_id: str
    content: str
    is_error: bool = False


# Adapters loop at most this many tool-call round-trips per chat() call
# before giving up and returning whatever text it has - guarantees
# termination regardless of what the model does.
MAX_TOOL_ITERATIONS = 8
