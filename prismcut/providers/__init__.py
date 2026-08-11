"""Provider adapter factory."""
from __future__ import annotations

from .base import Adapter, ChatMessage
from ..core.http import NotSupported, ProviderError

_ADAPTERS: dict[str, type] = {}


def _load() -> None:
    if _ADAPTERS:
        return
    from .google_ai import GoogleAI
    from .openai_api import OpenAI
    from .anthropic_api import Anthropic
    from .xai import XAI
    from .deepseek import DeepSeek
    from .minimax import MiniMax
    from .seedance import Seedance
    from .stability import Stability
    from .bfl import BFL
    from .falai import FalAI
    from .replicate_api import Replicate
    from .elevenlabs import ElevenLabs
    from .suno import Suno
    from .custom_openai import CustomOpenAI
    for cls in (GoogleAI, OpenAI, Anthropic, XAI, DeepSeek, MiniMax, Seedance,
                Stability, BFL, FalAI, Replicate, ElevenLabs, Suno, CustomOpenAI):
        _ADAPTERS[cls.name] = cls


def adapter_class(name: str) -> type:
    _load()
    if name not in _ADAPTERS:
        raise ProviderError(f"Unknown provider '{name}'")
    return _ADAPTERS[name]


def make_adapter(name: str, keychain, registry) -> Adapter:
    spec = registry.provider(name)
    if spec is None:
        raise ProviderError(f"Provider '{name}' missing from models.json")
    return adapter_class(name)(keychain, spec)


__all__ = ["Adapter", "ChatMessage", "ProviderError", "NotSupported",
           "make_adapter", "adapter_class"]
