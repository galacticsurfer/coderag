# LLM providers

The retrieval platform is LLM-independent: `search` and `context` need **no** provider. Only
`ask` calls an LLM, through the `LLMProvider` interface (`coderag/llm/base.py`).

## Interface

```python
class LLMProvider(Protocol):
    def generate(self, request: LLMRequest) -> LLMResponse: ...
    def generate_stream(self, request: LLMRequest) -> Iterator[str]: ...
    def get_usage(self) -> Usage: ...
```

`LLMResponse` carries the text plus `Usage` (`input_tokens`, `output_tokens`,
`cached_input_tokens`, latency, model, success). Usage is persisted to `llm_requests`.

## AnthropicClaudeProvider

Ships in `coderag/llm/anthropic.py`, talking to the Messages API over `httpx`. Because the
transport is plain HTTP, the same provider adapts to:

- **Anthropic API** — default (`CODERAG_ANTHROPIC_BASE_URL=https://api.anthropic.com`).
- **Internal proxy** — point `ANTHROPIC_BASE_URL` at your gateway; auth header is configurable.
- **AWS Bedrock** — subclass and swap the transport/signing (SigV4) and request shape; the
  retrieval layer is untouched. Documented as an extension point.

Configuration is entirely env-driven (`CODERAG_ANTHROPIC_*`). **Credentials are never
committed or logged.**

## Adding another provider

Implement `LLMProvider`, register it in the provider factory, and select it via
`CODERAG_LLM_PROVIDER`. Keep all transport-specific logic inside the provider — retrieval,
context building, and token accounting stay provider-agnostic (ADR-005).

## No-LLM mode

`CODERAG_LLM_PROVIDER=null` (or missing credentials) leaves `ask` disabled while everything
else works — useful for CI and for evaluating retrieval in isolation.
