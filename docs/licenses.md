# Dependency Licenses

CodeRAG itself is **Apache-2.0**. Everything except the external LLM endpoint is built on
open-source components. This table is a review aid, not legal advice — verify before
enterprise deployment.

## Core dependencies

| Package | License | Notes |
|---------|---------|-------|
| fastapi, starlette | MIT | API framework |
| uvicorn | BSD-3-Clause | ASGI server |
| pydantic, pydantic-settings | MIT | config/validation |
| SQLAlchemy | MIT | ORM |
| alembic | MIT | migrations |
| **psycopg (v3)** | **LGPL-3.0** | ⚠️ **Review for enterprise.** Dynamically linked; typically acceptable, but confirm with legal. BSD alternative: `pg8000`. |
| pgvector (Python bindings) | MIT | vector column type |
| pgvector (Postgres extension) | PostgreSQL License | permissive |
| tree-sitter, tree-sitter-python | MIT | parsing |
| typer, click | MIT | CLI |
| rich | MIT | CLI output |
| httpx | BSD-3-Clause | LLM transport |
| structlog | MIT / Apache-2.0 (dual) | logging |

## Optional extras

| Package | License | Extra | Notes |
|---------|---------|-------|-------|
| sentence-transformers | Apache-2.0 | `embeddings` | local embeddings |
| transformers | Apache-2.0 | (transitive) | |
| torch | BSD-3-Clause | (transitive) | large download |
| tiktoken | MIT | `tokens` | downloads BPE ranks on first use (network) |
| prometheus-client | Apache-2.0 | `metrics` | |
| pylint | GPL-2.0 | `analyzers` | ⚠️ invoked as a **subprocess/tool**, not imported/linked — using its output does not make CodeRAG a derivative work. Still, confirm your usage. |
| flake8 | MIT | `analyzers` | |

## Model weights

| Model | License | Notes |
|-------|---------|-------|
| sentence-transformers/all-MiniLM-L6-v2 | Apache-2.0 | default local embedding model |
| cross-encoder/ms-marco-MiniLM-L-6-v2 | Apache-2.0 | optional reranker |

## Dev/test only (never shipped)

| Package | License | Notes |
|---------|---------|-------|
| pgserver | Apache-2.0 (wheel); bundles PostgreSQL (PostgreSQL License) + pgvector | rootless Postgres for tests |
| pytest, pytest-cov | MIT | |
| ruff, mypy | MIT | |

## Flags summary

- **psycopg 3 = LGPL-3.0** — the one dependency warranting explicit legal review; `pg8000`
  (BSD) is a documented fallback.
- **pylint = GPL-2.0** — used only as an external tool via subprocess; keep it that way.
