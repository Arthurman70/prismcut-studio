"""Tool-calling round-trip per adapter wire format. Every HTTP call is
mocked (monkeypatched prismcut.core.http.request_json) - this test suite
must never make a real network call, per project convention."""
from prismcut.core import http as http_mod
from prismcut.core.registry import ProviderSpec
from prismcut.providers.anthropic_api import Anthropic
from prismcut.providers.base import ChatMessage
from prismcut.providers.google_ai import GoogleAI
from prismcut.providers.openai_compat import OpenAICompatChat
from prismcut.providers.tools import Tool, ToolCall, ToolResult


class FakeKeychain:
    def get_key(self, provider, env=""):
        return "fake-key"

    def get(self, key, default=None):
        return default


SPEC = ProviderSpec(name="fake", label="Fake", base_url="https://fake.example/api")

WEATHER_TOOL = Tool(name="get_weather", description="Get the weather",
                    parameters={"type": "object", "properties": {"city": {"type": "string"}},
                               "required": ["city"]})


def _fake_on_tool_call(calls_seen):
    def handler(call: ToolCall) -> ToolResult:
        calls_seen.append(call)
        assert call.name == "get_weather"
        assert call.arguments == {"city": "Boston"}
        return ToolResult(tool_call_id=call.id, content="72F and sunny")
    return handler


def test_openai_compat_tool_call_round_trip(monkeypatch):
    responses = [
        {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "get_weather", "arguments": '{"city": "Boston"}'}}]}}]},
        {"choices": [{"message": {"role": "assistant", "content": "It's 72F and sunny in Boston."}}]},
    ]
    calls_made = []

    def fake_request_json(method, url, **kw):
        calls_made.append(kw.get("json_body"))
        return responses.pop(0)

    monkeypatch.setattr(http_mod, "request_json", fake_request_json)
    adapter = OpenAICompatChat(FakeKeychain(), SPEC)
    calls_seen = []
    text = adapter.chat("fake-model", [ChatMessage("user", "What's the weather in Boston?")],
                        tools=[WEATHER_TOOL], on_tool_call=_fake_on_tool_call(calls_seen))

    assert text == "It's 72F and sunny in Boston."
    assert len(calls_seen) == 1 and calls_seen[0].id == "call_1"
    # second request must include the tool result fed back
    second_body = calls_made[1]
    assert second_body["messages"][-1] == {"role": "tool", "tool_call_id": "call_1",
                                           "content": "72F and sunny"}
    assert second_body["tools"][0]["function"]["name"] == "get_weather"


def test_google_ai_tool_call_round_trip(monkeypatch):
    responses = [
        {"candidates": [{"content": {"role": "model", "parts": [
            {"functionCall": {"name": "get_weather", "id": "fc1", "args": {"city": "Boston"}}}]}}]},
        {"candidates": [{"content": {"parts": [{"text": "It's 72F and sunny in Boston."}]}}]},
    ]
    calls_made = []

    def fake_request_json(method, url, **kw):
        calls_made.append(kw.get("json_body"))
        return responses.pop(0)

    monkeypatch.setattr(http_mod, "request_json", fake_request_json)
    adapter = GoogleAI(FakeKeychain(), SPEC)
    calls_seen = []
    text = adapter.chat("gemini-fake", [ChatMessage("user", "What's the weather in Boston?")],
                        tools=[WEATHER_TOOL], on_tool_call=_fake_on_tool_call(calls_seen))

    assert text == "It's 72F and sunny in Boston."
    assert len(calls_seen) == 1 and calls_seen[0].id == "fc1"
    second_body = calls_made[1]
    fr = second_body["contents"][-1]["parts"][0]["functionResponse"]
    assert fr["name"] == "get_weather" and fr["response"]["result"] == "72F and sunny"
    assert second_body["tools"][0]["functionDeclarations"][0]["name"] == "get_weather"


def test_anthropic_tool_use_round_trip(monkeypatch):
    responses = [
        {"content": [{"type": "tool_use", "id": "toolu_1", "name": "get_weather",
                     "input": {"city": "Boston"}}]},
        {"content": [{"type": "text", "text": "It's 72F and sunny in Boston."}]},
    ]
    calls_made = []

    def fake_request_json(method, url, **kw):
        calls_made.append(kw.get("json_body"))
        return responses.pop(0)

    monkeypatch.setattr(http_mod, "request_json", fake_request_json)
    adapter = Anthropic(FakeKeychain(), SPEC)
    calls_seen = []
    text = adapter.chat("claude-fake", [ChatMessage("user", "What's the weather in Boston?")],
                        tools=[WEATHER_TOOL], on_tool_call=_fake_on_tool_call(calls_seen))

    assert text == "It's 72F and sunny in Boston."
    assert len(calls_seen) == 1 and calls_seen[0].id == "toolu_1"
    second_body = calls_made[1]
    result_block = second_body["messages"][-1]["content"][0]
    assert result_block == {"type": "tool_result", "tool_use_id": "toolu_1",
                            "content": "72F and sunny", "is_error": False}
    assert second_body["tools"][0]["name"] == "get_weather"


def test_tool_loop_terminates_without_infinite_calling(monkeypatch):
    """If the model calls a tool every single time, the loop must still
    terminate (MAX_TOOL_ITERATIONS) rather than hang or loop forever."""
    call_count = {"n": 0}

    def fake_request_json(method, url, **kw):
        call_count["n"] += 1
        return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": f"call_{call_count['n']}", "type": "function",
             "function": {"name": "get_weather", "arguments": '{"city": "Boston"}'}}]}}]}

    monkeypatch.setattr(http_mod, "request_json", fake_request_json)
    adapter = OpenAICompatChat(FakeKeychain(), SPEC)
    text = adapter.chat("fake-model", [ChatMessage("user", "loop forever")],
                        tools=[WEATHER_TOOL], on_tool_call=_fake_on_tool_call([]))
    assert text == ""  # exhausted iterations, no final text - but it DID return
    from prismcut.providers.tools import MAX_TOOL_ITERATIONS
    assert call_count["n"] == MAX_TOOL_ITERATIONS
