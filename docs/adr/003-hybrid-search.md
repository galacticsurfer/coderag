# ADR-003: Hybrid search instead of embedding-only search

- **Status:** Accepted
- **Context:** Embeddings are great for "code that retries failed transactions" but bad at
  exact identifiers (`PAYMENT_RETRY_LIMIT`, `ERR_PAYMENT_102`, `/api/v2/payment`) and exact
  symbol lookups. Relying only on vectors misses the queries developers actually type.
- **Decision:** Run four retrievers and fuse them:
  1. **exact symbol** (qualified/name match, strongly prioritised),
  2. **lexical** (Postgres full-text over identifiers/signatures/docstrings),
  3. **semantic** (pgvector),
  4. **structural** (one-hop graph expansion from high-confidence hits).
  Merge with **Reciprocal Rank Fusion** (transparent, no per-source score calibration), with
  per-source weights in config. Every result records *why* it was retrieved.
- **Consequences:**
  - Robust across identifier lookups and natural-language questions.
  - RRF avoids fragile score normalisation across incomparable scales.
  - Ranking weights are tunable config, not magic constants in code.
- **Revisit when:** a learned fusion/reranker measurably beats RRF on our eval harness.
