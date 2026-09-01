"""Phase 6: Anthropic provider (mocked transport) — no real API calls."""

from __future__ import annotations

import json

import httpx
import pytest

from coderag.core.config import Settings
from coderag.llm.anthropic import AnthropicClaudeProvider
from coderag.llm.base import LLMRequest

SETTINGS = Settings(anthropic_api_key="test-key", anthropic_model="claude-sonnet-5")


def test_generate_builds_request_and_parses_usage():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.content)
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "model": "claude-sonnet-5",
                "content": [{"type": "text", "text": "Because the invoice is not reconciled."}],
                "usage": {"input_tokens": 1200, "output_tokens": 42,
                          "cache_read_input_tokens": 100},
            },
        )

    provider = AnthropicClaudeProvider(SETTINGS, transport=httpx.MockTransport(handler))
    resp = provider.generate(LLMRequest(prompt="why pending?", max_tokens=256))

    assert resp.text == "Because the invoice is not reconciled."
    assert resp.usage.input_tokens == 1200
    assert resp.usage.output_tokens == 42
    assert resp.usage.cached_input_tokens == 100
    assert resp.usage.success is True
    # correct wire format
    assert captured["url"].endswith("/v1/messages")
    assert captured["headers"]["x-api-key"] == "test-key"
    assert captured["headers"]["anthropic-version"]
    assert captured["body"]["model"] == "claude-sonnet-5"
    assert captured["body"]["max_tokens"] == 256
    assert captured["body"]["messages"][0]["content"] == "why pending?"


def test_generate_error_path_records_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "bad"}})

    provider = AnthropicClaudeProvider(SETTINGS, transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        provider.generate(LLMRequest(prompt="x"))
    assert provider.get_usage().success is False
    assert provider.get_usage().error


def test_generate_stream_yields_text_deltas():
    sse = (
        'data: {"type":"message_start","message":{"usage":{"input_tokens":10}}}\n\n'
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello "}}\n\n'
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"world"}}\n\n'
        'data: {"type":"message_delta","usage":{"output_tokens":2}}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse.encode(),
                              headers={"content-type": "text/event-stream"})

    provider = AnthropicClaudeProvider(SETTINGS, transport=httpx.MockTransport(handler))
    chunks = list(provider.generate_stream(LLMRequest(prompt="hi")))
    assert "".join(chunks) == "Hello world"
    assert provider.get_usage().output_tokens == 2


def test_missing_key_raises():
    with pytest.raises(RuntimeError):
        AnthropicClaudeProvider(Settings(anthropic_api_key=None))
