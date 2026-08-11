"""Any OpenAI-compatible endpoint (OpenRouter, Groq, Together, Ollama, LM Studio,
vLLM, a corporate gateway...). Set the base URL in API Keys ▸ advanced, add your
model ids in the Model Manager, done - this is the ""and others"" escape hatch.
"""
from __future__ import annotations

from .openai_compat import OpenAICompatChat


class CustomOpenAI(OpenAICompatChat):
    name = "custom"
    label = "Custom (OpenAI-compatible)"
