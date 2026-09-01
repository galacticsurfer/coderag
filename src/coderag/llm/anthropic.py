"""Anthropic Claude provider over the Messages API (HTTP).

Because the transport is plain HTTP via httpx, the same provider adapts to the
Anthropic API, an internal proxy (set ``anthropic_base_url``), or — with a
subclass swapping auth/signing — AWS Bedrock. Credentials come from config and
are never logged.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator

import httpx

from coderag.core.config import Settings, get_settings
from coderag.llm.base import LLMProvider, LLMRequest, LLMResponse, Usage


class AnthropicClaudeProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        if not self.settings.anthropic_api_key and client is None and transport is None:
            raise RuntimeError(
                "CODERAG_ANTHROPIC_API_KEY is not set. Retrieval works without an LLM; "
                "only `ask` needs credentials."
            )
        self._client = client or httpx.Client(
            base_url=self.settings.anthropic_base_url,
            timeout=self.settings.llm_timeout_seconds,
            transport=transport,
            headers={
                "x-api-key": self.settings.anthropic_api_key or "",
                "anthropic-version": self.settings.anthropic_version,
                "content-type": "application/json",
            },
        )
        self._last_usage = Usage()

    def _body(self, request: LLMRequest, stream: bool) -> dict:
        return {
            "model": request.model or self.settings.anthropic_model,
            "max_tokens": request.max_tokens or self.settings.llm_max_output_tokens,
            "temperature": request.temperature,
            "system": request.system,
            "messages": [{"role": "user", "content": request.prompt}],
            "stream": stream,
        }

    def generate(self, request: LLMRequest) -> LLMResponse:
        started = time.perf_counter()
        model = request.model or self.settings.anthropic_model
        try:
            resp = self._client.post("/v1/messages", json=self._body(request, stream=False))
            resp.raise_for_status()
            data = resp.json()
            text = "".join(
                block.get("text", "")
                for block in data.get("content", [])
                if block.get("type") == "text"
            )
            usage_raw = data.get("usage", {})
            usage = Usage(
                input_tokens=usage_raw.get("input_tokens", 0),
                output_tokens=usage_raw.get("output_tokens", 0),
                cached_input_tokens=usage_raw.get("cache_read_input_tokens"),
                model=data.get("model", model),
                latency_ms=(time.perf_counter() - started) * 1000,
                success=True,
            )
            self._last_usage = usage
            return LLMResponse(text=text, usage=usage)
        except Exception as exc:
            usage = Usage(
                model=model, latency_ms=(time.perf_counter() - started) * 1000,
                success=False, error=type(exc).__name__ + ": " + str(exc)[:200],
            )
            self._last_usage = usage
            raise

    def generate_stream(self, request: LLMRequest) -> Iterator[str]:
        started = time.perf_counter()
        model = request.model or self.settings.anthropic_model
        usage = Usage(model=model)
        with self._client.stream(
            "POST", "/v1/messages", json=self._body(request, stream=True)
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[len("data:") :].strip()
                if payload in ("", "[DONE]"):
                    continue
                event = json.loads(payload)
                etype = event.get("type")
                if etype == "message_start":
                    usage.input_tokens = (
                        event.get("message", {}).get("usage", {}).get("input_tokens", 0)
                    )
                elif etype == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        yield delta.get("text", "")
                elif etype == "message_delta":
                    usage.output_tokens = event.get("usage", {}).get("output_tokens", 0)
        usage.latency_ms = (time.perf_counter() - started) * 1000
        usage.success = True
        self._last_usage = usage
