"""Null LLM provider: retrieval works without an LLM; `ask` needs a real one."""

from __future__ import annotations

from coderag.llm.base import LLMProvider, LLMRequest, LLMResponse


class NullLLMProvider(LLMProvider):
    name = "null"

    def generate(self, request: LLMRequest) -> LLMResponse:
        raise RuntimeError(
            "No LLM provider configured (CODERAG_LLM_PROVIDER=null). "
            "`search` and `context` work without one; set CODERAG_LLM_PROVIDER=anthropic "
            "and CODERAG_ANTHROPIC_API_KEY to enable `ask`."
        )
