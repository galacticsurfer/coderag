# Retrieval

CodeRAG uses **hybrid retrieval**: four methods whose results are fused with Reciprocal Rank
Fusion (RRF). Every candidate records *why* it was retrieved.

## The four methods

1. **Exact symbol** (`retrieval/symbol.py`) — matches a query that looks like a symbol
   (`PaymentService.retry_payment`, `retry_payment`) against `qualified_name` / `symbol_name`.
   Strongly prioritised (highest fusion weight).
2. **Lexical** (`retrieval/lexical.py`) — Postgres full-text search over the
   `search_document` (identifiers + signature + docstring). Handles `PAYMENT_RETRY_LIMIT`,
   `ERR_PAYMENT_102`, `/api/v2/payment`, etc. that embeddings miss.
3. **Semantic** (`retrieval/semantic.py`) — embeds the query and does a pgvector cosine
   search. Handles paraphrases ("code that retries failed transactions").
4. **Structural** (`retrieval/graph.py`) — from high-confidence hits, expands **one hop** to
   parents, children, callers, callees, imports, and tests. Bounded by depth, candidate
   count, and a token cap.

## Fusion (RRF)

For a document ranked at position `r` (1-based) by a source, RRF contributes
`weight / (k + r)`. Scores are summed across sources. `k` (default 60) and per-source weights
live in `Settings` (`rrf_k`, `weight_symbol/lexical/semantic/graph`). RRF needs no score
normalisation across incomparable scales — that's why we prefer it for v1.

Each merged candidate keeps the set of **reasons** (`exact_symbol`, `lexical`, `semantic`,
`graph_call`, `graph_test`, `graph_parent`, …) and its per-source ranks.

## Reranking (optional)

`retrieval/reranking.py` provides a deterministic reranker by default and an optional
SentenceTransformers CrossEncoder. Reranking is **off by default** (adds latency); the eval
harness measures its quality gain vs cost.

## Scoping & isolation

Every retriever filters on `repository_id`. There is no query path that reads symbols or
embeddings without a repository scope (see ADR-006 and the isolation test).
