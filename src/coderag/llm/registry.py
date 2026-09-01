"""LLM provider selection."""

from __future__ import annotations

from coderag.core.config import Settings, get_settings
from coderag.llm.base import LLMProvider


def get_llm_provider(settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    if settings.llm_provider == "anthropic":
        from coderag.llm.anthropic import AnthropicClaudeProvider

        return AnthropicClaudeProvider(settings)
    from coderag.llm.null import NullLLMProvider

    return NullLLMProvider()
