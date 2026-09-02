"""Central configuration.

All tunable behaviour (ranking weights, budgets, model names, provider
selection) lives here so there are no magic constants scattered through the
code. Values are read from the environment / a ``.env`` file.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

COMPOSE_DATABASE_URL = "postgresql+psycopg://coderag:coderag@localhost:5432/coderag"


def _default_database_url() -> str:
    """Fall back to the URL written by `coderag localdb start`, else compose's."""
    from pathlib import Path

    try:
        value = (Path.home() / ".coderag" / "database_url").read_text().strip()
    except OSError:
        return COMPOSE_DATABASE_URL
    return value or COMPOSE_DATABASE_URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CODERAG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- Database -------------------------------------------------------
    # A libpq URL. In dev/test the pgserver fixture overrides this.
    # Resolution order: CODERAG_DATABASE_URL env/.env -> the URL recorded by
    # `coderag localdb start` -> the docker-compose default.
    database_url: str = Field(default_factory=lambda: _default_database_url())
    db_echo: bool = False

    # Default repository name used when a request doesn't name one (handy for the
    # MCP server / CLI when several repositories are indexed).
    default_repository: str | None = None

    # ---- Embeddings -----------------------------------------------------
    # "hashing"  -> deterministic, dependency-free (default; offline, great for tests)
    # "sentence_transformer" -> local SentenceTransformers model (needs `embeddings` extra)
    embedding_provider: str = "hashing"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_device: str = "cpu"
    embedding_batch_size: int = 32
    # Dimension of the hashing embedder (the ST provider reports its own).
    embedding_dimension: int = 384

    # ---- Token counting -------------------------------------------------
    # "heuristic" (offline, deterministic) or "tiktoken" (needs `tokens` extra).
    token_counter: str = "heuristic"
    tiktoken_encoding: str = "cl100k_base"

    # ---- Retrieval / fusion --------------------------------------------
    # Per-source candidate limits (how many each retriever returns).
    symbol_limit: int = 10
    lexical_limit: int = 30
    semantic_limit: int = 30
    # Reciprocal Rank Fusion constant and per-source weights.
    rrf_k: int = 60
    weight_symbol: float = 2.0
    weight_lexical: float = 1.0
    weight_semantic: float = 1.0
    weight_graph: float = 0.7
    # Max merged candidates carried into ranking.
    max_candidates: int = 60

    # ---- Graph expansion (bounded) -------------------------------------
    graph_enabled: bool = True
    graph_max_depth: int = 1
    graph_max_candidates: int = 20
    graph_max_tokens: int = 6000

    # ---- Reranking (optional) ------------------------------------------
    reranker_enabled: bool = False
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    # ---- Context builder ------------------------------------------------
    max_context_tokens: int = 12000
    # Reserve tokens for the scaffolding/instructions of the prompt.
    context_overhead_tokens: int = 600

    # ---- LLM provider ---------------------------------------------------
    llm_provider: str = "anthropic"  # "anthropic" | "null"
    anthropic_api_key: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_model: str = "claude-sonnet-5"
    anthropic_version: str = "2023-06-01"
    llm_max_output_tokens: int = 1024
    llm_timeout_seconds: float = 60.0

    # ---- Cost estimation -------------------------------------------------
    # USD per million tokens, used only to turn token counts into indicative
    # dollar figures. Defaults are Claude Opus 4.8 list prices; override for your
    # model/plan. These are ESTIMATES at configured prices — not billing data.
    price_input_per_mtok: float = 5.00
    price_output_per_mtok: float = 25.00

    # ---- Analyzers (Phase 10) ------------------------------------------
    max_fix_attempts: int = 2

    # ---- Security -------------------------------------------------------
    # Extra path fragments to ignore during indexing (comma-separated env).
    extra_ignore: list[str] = Field(default_factory=list)
    # If True, structured logs may include short code snippets (never full files).
    log_code_snippets: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
