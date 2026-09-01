# ADR-007: Token budgeting strategy

- **Status:** Accepted
- **Context:** The product's whole reason to exist is *fewer input tokens at equal quality*.
  We must never rely on the model's context-window maximum, and we must be able to prove
  savings.
- **Decision:**
  - A pluggable `TokenCounter` (offline `HeuristicTokenCounter` default; optional
    `TiktokenCounter`) estimates tokens *before* any LLM call. The **authoritative** counts
    come back from the provider's usage report and are stored in `llm_requests`; we never
    present an estimate as the billed number.
  - The `ContextBuilder` fills a hard `MAX_CONTEXT_TOKENS` budget by **priority**: target
    symbol → changed code/diff → direct implementation → direct dependencies → callers →
    tests → semantically-similar → surrounding context.
  - **Drop whole low-ranked symbols** rather than truncating every symbol arbitrarily.
    Overlapping code (e.g. a method already inside its class chunk) is **deduplicated** so it
    is never sent twice.
  - Graph expansion is separately bounded (depth, candidate count, token cap).
  - Every stage records `candidate_tokens`, `context_tokens`, and `dropped_tokens`, and the
    evaluation harness compares a naive baseline against the RAG context.
- **Consequences:**
  - Egress is bounded and observable; savings are measured, not asserted.
  - Priority-based dropping keeps the highest-value code intact under pressure.
- **Revisit when:** we add model-specific tokenizers or per-request dynamic budgets.
