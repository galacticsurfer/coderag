# ADR-002: Structural chunks instead of fixed token/character chunks

- **Status:** Accepted
- **Context:** Generic RAG splits text every N tokens. For code that destroys the unit of
  meaning — a function body severed mid-statement is useless as context and pollutes
  embeddings.
- **Decision:** Chunk on **code constructs** — module, class, function, method, test — using
  Tree-sitter. Each symbol is a chunk with precise line ranges, signature, docstring, and
  qualified name. For pathologically large symbols we apply a documented fallback (keep the
  signature + docstring + head/tail, mark as truncated) rather than an arbitrary mid-body cut.
- **Consequences:**
  - Retrieval and context are addressable by real symbols (`PaymentService.retry_payment`).
  - Embeddings carry structural metadata, improving semantic matches.
  - Slightly more parsing work than naive splitting; handled once at index time and cached by
    `source_hash`.
- **Revisit when:** we need sub-function retrieval (e.g. very long methods) — then add
  intra-symbol windows *within* the structural boundary, never across it.
