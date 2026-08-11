"""DeepSeek chat (OpenAI-compatible, text-only).

Docs: https://api-docs.deepseek.com - key: https://platform.deepseek.com/api_keys
"""
from __future__ import annotations

from .openai_compat import OpenAICompatChat


class DeepSeek(OpenAICompatChat):
    name = "deepseek"
    label = "DeepSeek"
    supports_images_in_chat = False
