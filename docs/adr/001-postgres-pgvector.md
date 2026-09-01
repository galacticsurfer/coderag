# ADR-001: PostgreSQL + pgvector instead of a dedicated vector database

- **Status:** Accepted
- **Context:** We need symbol metadata, full-text search, a relationship graph, telemetry,
  *and* vector search. A dedicated vector DB (Milvus/Qdrant/Weaviate) adds another datastore
  to operate, sync, and secure, and splits the source of truth.
- **Decision:** Use PostgreSQL as the single datastore, with pgvector for embeddings and
  built-in full-text search (`tsvector`) for lexical retrieval. Start with **exact** vector
  scan for correctness; add an HNSW index only when dataset size proves it necessary.
- **Consequences:**
  - One datastore to run, back up, secure, and scope by `repository_id` — simpler ops and
    stronger isolation guarantees.
  - Transactional consistency between symbols, embeddings, and relationships.
  - Exact scan is O(n) per query; fine at MVP scale, revisit with HNSW + a benchmark trigger.
  - We keep `embedding_model/version/dimension` per row and a dimension-less vector column so
    the embedding model can change without a schema migration (re-embedding supported).
- **Revisit when:** p95 vector search latency exceeds target at the repo sizes we care about,
  *after* trying an HNSW index.
