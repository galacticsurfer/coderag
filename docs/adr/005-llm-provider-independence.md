# ADR-005: Provider-independent LLM (and embedding) layer

- **Status:** Accepted
- **Context:** The company's Claude may be reached via the Anthropic API, AWS Bedrock, or an
  internal proxy. Retrieval quality and token accounting must not depend on any of that. Code
  must also never leave infra just to compute embeddings.
- **Decision:** Define `LLMProvider` (`generate`, `generate_stream`, `get_usage`) and
  `EmbeddingProvider` interfaces. Ship `AnthropicClaudeProvider` (HTTP transport, so
  Bedrock/proxy variants are drop-in) and a local `SentenceTransformerEmbeddingProvider`
  (plus a deterministic `HashingEmbeddingProvider` default for offline/dev/test). The entire
  retrieval + context pipeline runs with **no LLM configured**.
- **Consequences:**
  - `search` and `context` work without credentials; only `ask` needs a provider.
  - Transport-specific logic lives in the provider, never in retrieval.
  - Usage (input/output/cached tokens, latency) is recorded uniformly in `llm_requests`.
- **Revisit when:** adding a provider with a fundamentally different API shape — extend the
  interface minimally rather than leaking provider details upward.
