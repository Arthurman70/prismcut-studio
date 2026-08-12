"""Adapter base class - the contract every provider implements.

Adapters are Qt-free. All blocking work happens inside jobs (see core.jobs).
Every generated asset is saved into the app's `generated` dir and the file
path(s) returned, so the UI can drop results straight into the project bin.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..core import paths
from ..core.captions import Segment
from ..core.http import NotSupported, ProviderError
from ..core.registry import ProviderSpec
from .tools import Tool, ToolCall, ToolResult


@dataclass
class ChatMessage:
    role: str                       # "user" | "assistant" | "tool"
    text: str
    attachments: list = field(default_factory=list)   # file paths (image/audio/video)
    tool_calls: list = field(default_factory=list)    # list[ToolCall] - set on "assistant" turns that called tools
    tool_call_id: str = ""                            # set on "tool" turns - which call this is the result of


CancelFn = Callable[[], bool]
DeltaFn = Callable[[str], None]
ProgressFn = Callable[[int, str], None]


class Adapter:
    name = "base"
    label = "Base"

    def __init__(self, keychain, spec: ProviderSpec):
        self.keychain = keychain
        self.spec = spec

    # ------------------------------------------------------------- plumbing
    def key(self, required: bool = True) -> Optional[str]:
        k = self.keychain.get_key(self.name, self.spec.key_env)
        if required and not k:
            raise ProviderError(
                f"No API key set for {self.spec.label}. Add one in  AI ▸ API Keys…  "
                f"(get a key at {self.spec.key_url or 'the provider site'}).")
        return k

    def base_url(self) -> str:
        return str(self.keychain.get(f"providers/{self.name}/base_url", "")
                   or self.spec.base_url).rstrip("/")

    def _out(self, stem: str, ext: str) -> Path:
        return paths.unique_path(paths.generated_dir(), f"{self.name}_{stem}", ext)

    def _save_bytes(self, data: bytes, stem: str, ext: str) -> str:
        p = self._out(stem, ext)
        p.write_bytes(data)
        return str(p)

    @staticmethod
    def _poll(fetch: Callable[[], Optional[object]], *, interval: float = 4.0,
              timeout: float = 900.0, progress: Optional[ProgressFn] = None,
              should_cancel: Optional[CancelFn] = None, label: str = "Generating") -> object:
        """Generic long-poll helper: fetch() returns result or None to keep waiting."""
        t0 = time.time()
        n = 0
        while True:
            if should_cancel and should_cancel():
                raise ProviderError("Cancelled")
            result = fetch()
            if result is not None:
                return result
            n += 1
            elapsed = time.time() - t0
            if elapsed > timeout:
                raise ProviderError(f"Timed out after {int(elapsed)}s waiting for the provider.")
            if progress:
                progress(-1, f"{label}… {int(elapsed)}s")
            time.sleep(interval)

    # ----------------------------------------------------------- capabilities
    def chat(self, model: str, messages: list[ChatMessage], system: str = "",
             temperature: float = 0.7, on_delta: Optional[DeltaFn] = None,
             should_cancel: Optional[CancelFn] = None,
             tools: Optional[list[Tool]] = None,
             on_tool_call: Optional[Callable[[ToolCall], ToolResult]] = None) -> str:
        """tools/on_tool_call are optional - only Google, the OpenAI-compatible
        family, and Anthropic implement them. When tools is given, on_delta
        is ignored (streaming + tool-call parsing isn't implemented for v1) -
        the adapter internally loops: send -> if the model calls a tool,
        invoke on_tool_call(call) synchronously, feed the ToolResult back,
        repeat (capped at tools.MAX_TOOL_ITERATIONS) -> return final text."""
        raise NotSupported(f"{self.spec.label} has no chat support in PrismCut yet.")

    def generate_image(self, model: str, prompt: str, params: Optional[dict] = None,
                       refs: Optional[list] = None) -> list[str]:
        raise NotSupported(f"{self.spec.label} has no image generation support yet.")

    def edit_image(self, model: str, prompt: str, image, mask=None,
                   params: Optional[dict] = None) -> list[str]:
        raise NotSupported(f"{self.spec.label} has no image editing support yet.")

    def generate_video(self, model: str, prompt: str, params: Optional[dict] = None,
                       image=None, audio=None, progress: Optional[ProgressFn] = None,
                       should_cancel: Optional[CancelFn] = None) -> str:
        """audio is an optional path to a driving audio track, for the rare
        model that hard-syncs generated video to it natively (see
        core.pipeline_orchestrator.resolve_video_plan - as of this writing
        no configured model actually implements this branch; it's here so
        one becoming available later is a models.json + small per-adapter
        passthrough, not a signature change)."""
        raise NotSupported(f"{self.spec.label} has no video generation support yet.")

    def tts(self, model: str, text: str, voice: str = "", params: Optional[dict] = None) -> str:
        raise NotSupported(f"{self.spec.label} has no text-to-speech support yet.")

    def music(self, model: str, prompt: str, params: Optional[dict] = None,
              progress: Optional[ProgressFn] = None,
              should_cancel: Optional[CancelFn] = None) -> str:
        raise NotSupported(f"{self.spec.label} has no music generation support yet.")

    def sound_effect(self, model: str, text: str, params: Optional[dict] = None) -> str:
        raise NotSupported(f"{self.spec.label} has no SFX support yet.")

    def transcribe(self, model: str, audio) -> str:
        raise NotSupported(f"{self.spec.label} has no transcription support yet.")

    def transcribe_segments(self, model: str, audio) -> list[Segment]:
        """Timestamped transcription for SRT export - a separate method
        from transcribe() rather than a richer return type there, since
        most transcribe-capable models (Gemini's chat-based transcription,
        gpt-4o-transcribe) have no segment/timestamp API to return, and
        overloading transcribe()'s return type would force its two existing
        plain-text callers to branch on str-vs-list for no benefit to them."""
        raise NotSupported(f"{self.spec.label} has no per-segment (SRT) transcription support.")

    def test_key(self) -> tuple[bool, str]:
        try:
            self.key()
            return True, "Key present (not validated - provider has no cheap ping endpoint wired)."
        except ProviderError as e:
            return False, str(e)
