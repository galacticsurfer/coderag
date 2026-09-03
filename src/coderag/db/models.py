"""SQLAlchemy 2.0 ORM models — the persisted code index and telemetry.

Design notes:
  * Every retrieval-relevant table carries ``repository_id`` so queries can
    (and must) be scoped to a single repository — see ``security/authz.py``.
  * ``symbols.fts`` is a generated ``tsvector`` (Postgres full-text search).
  * ``symbol_embeddings.embedding`` uses a dimension-less pgvector column so
    the embedding model (and its dimension) can change without a schema
    migration; the concrete dimension is recorded per row.
"""

from __future__ import annotations

import datetime as dt

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    Computed,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSON}


TS = dt.datetime


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    local_path: Mapped[str] = mapped_column(String(1024))
    default_branch: Mapped[str] = mapped_column(String(255), default="main")
    indexed_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[TS] = mapped_column(server_default=func.now())
    updated_at: Mapped[TS] = mapped_column(server_default=func.now(), onupdate=func.now())


class SourceFile(Base):
    __tablename__ = "source_files"
    __table_args__ = (
        UniqueConstraint("repository_id", "path", name="uq_source_files_repo_path"),
        Index("ix_source_files_repo", "repository_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE")
    )
    path: Mapped[str] = mapped_column(String(1024))
    language: Mapped[str] = mapped_column(String(32))
    content_hash: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    # Whole-file token count, recorded at index time so the "what would reading
    # this file have cost?" baseline needs no file I/O at query time.
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    indexed_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[TS] = mapped_column(server_default=func.now())
    updated_at: Mapped[TS] = mapped_column(server_default=func.now(), onupdate=func.now())


class Symbol(Base):
    __tablename__ = "symbols"
    __table_args__ = (
        Index("ix_symbols_repo", "repository_id"),
        Index("ix_symbols_repo_qualified", "repository_id", "qualified_name"),
        Index("ix_symbols_repo_type", "repository_id", "symbol_type"),
        Index("ix_symbols_path", "repository_id", "file_path"),
        Index("ix_symbols_source_hash", "source_hash"),
        Index("ix_symbols_commit", "commit_sha"),
        Index("ix_symbols_fts", "fts", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE")
    )
    source_file_id: Mapped[int] = mapped_column(
        ForeignKey("source_files.id", ondelete="CASCADE")
    )
    file_path: Mapped[str] = mapped_column(String(1024))
    language: Mapped[str] = mapped_column(String(32))

    symbol_name: Mapped[str] = mapped_column(String(512))
    qualified_name: Mapped[str] = mapped_column(String(1024))
    symbol_type: Mapped[str] = mapped_column(String(32))  # module|class|function|method
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)

    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    parent_symbol_id: Mapped[int | None] = mapped_column(
        ForeignKey("symbols.id", ondelete="CASCADE"), nullable=True
    )

    source_code: Mapped[str] = mapped_column(Text)
    docstring: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_hash: Mapped[str] = mapped_column(String(64))
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, default=0)

    # Text feeding full-text search (identifiers + signature + docstring).
    search_document: Mapped[str] = mapped_column(Text, default="")
    fts: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', search_document)", persisted=True),
    )

    created_at: Mapped[TS] = mapped_column(server_default=func.now())
    updated_at: Mapped[TS] = mapped_column(server_default=func.now(), onupdate=func.now())

    parent: Mapped[Symbol | None] = relationship(
        "Symbol", remote_side="Symbol.id", back_populates="children"
    )
    children: Mapped[list[Symbol]] = relationship(
        "Symbol", back_populates="parent", viewonly=True
    )


class SymbolRelationship(Base):
    __tablename__ = "symbol_relationships"
    __table_args__ = (
        Index("ix_rel_repo_source", "repository_id", "source_symbol_id"),
        Index("ix_rel_repo_target", "repository_id", "target_symbol_id"),
        Index("ix_rel_type", "relationship_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE")
    )
    source_symbol_id: Mapped[int] = mapped_column(
        ForeignKey("symbols.id", ondelete="CASCADE")
    )
    # Target may be unresolved (an external/未-indexed name), so it is nullable
    # and we also keep the raw target_name.
    target_symbol_id: Mapped[int | None] = mapped_column(
        ForeignKey("symbols.id", ondelete="CASCADE"), nullable=True
    )
    target_name: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    relationship_type: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    meta: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[TS] = mapped_column(server_default=func.now())


class SymbolEmbedding(Base):
    __tablename__ = "symbol_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "symbol_id", "embedding_model", "embedding_version",
            name="uq_embedding_symbol_model",
        ),
        Index("ix_embedding_repo", "repository_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE")
    )
    symbol_id: Mapped[int] = mapped_column(ForeignKey("symbols.id", ondelete="CASCADE"))
    source_hash: Mapped[str] = mapped_column(String(64))
    embedding_model: Mapped[str] = mapped_column(String(255))
    embedding_version: Mapped[str] = mapped_column(String(64))
    embedding_dimension: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list[float]] = mapped_column(Vector())  # dimension-less
    created_at: Mapped[TS] = mapped_column(server_default=func.now())


class IndexingRun(Base):
    __tablename__ = "indexing_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE")
    )
    mode: Mapped[str] = mapped_column(String(16))  # full|incremental
    status: Mapped[str] = mapped_column(String(16), default="running")
    from_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    files_indexed: Mapped[int] = mapped_column(Integer, default=0)
    files_deleted: Mapped[int] = mapped_column(Integer, default=0)
    symbols_indexed: Mapped[int] = mapped_column(Integer, default=0)
    symbols_deleted: Mapped[int] = mapped_column(Integer, default=0)
    embeddings_created: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[TS] = mapped_column(server_default=func.now())
    finished_at: Mapped[TS | None] = mapped_column(nullable=True)


class QueryRecord(Base):
    __tablename__ = "queries"

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE")
    )
    query_text: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(16))  # search|context|ask
    candidates_found: Mapped[int] = mapped_column(Integer, default=0)
    candidates_selected: Mapped[int] = mapped_column(Integer, default=0)
    candidate_tokens: Mapped[int] = mapped_column(Integer, default=0)
    context_tokens: Mapped[int] = mapped_column(Integer, default=0)
    dropped_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # Counterfactual: tokens it would have cost to read the whole files that the
    # selected symbols came from (the "just open the files" baseline).
    baseline_tokens: Mapped[int] = mapped_column(Integer, default=0)
    baseline_files: Mapped[int] = mapped_column(Integer, default=0)
    retrieval_latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[TS] = mapped_column(server_default=func.now())


class RetrievalResult(Base):
    __tablename__ = "retrieval_results"
    __table_args__ = (Index("ix_retrieval_query", "query_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    query_id: Mapped[int] = mapped_column(ForeignKey("queries.id", ondelete="CASCADE"))
    symbol_id: Mapped[int] = mapped_column(ForeignKey("symbols.id", ondelete="CASCADE"))
    rank: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float)
    reasons: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[TS] = mapped_column(server_default=func.now())


class LLMRequest(Base):
    __tablename__ = "llm_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    query_id: Mapped[int | None] = mapped_column(
        ForeignKey("queries.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Tokens written to the provider's prompt cache this request (billed ~1.25x).
    cache_creation_input_tokens: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    # Characters of tool_result content observed in the request body (counts
    # only — content itself is never stored). Feeds the --compress diagnostic.
    tool_result_chars: Mapped[int] = mapped_column(Integer, default=0)
    token_lean_active: Mapped[bool] = mapped_column(Boolean, default=False)
    requested_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    compression_chars_saved: Mapped[int] = mapped_column(Integer, default=0)
    cap_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_cache_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    terse_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    tool_schema_chars: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[TS] = mapped_column(server_default=func.now())


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    dataset: Mapped[str] = mapped_column(String(1024))
    recall_at_1: Mapped[float] = mapped_column(Float, default=0.0)
    recall_at_3: Mapped[float] = mapped_column(Float, default=0.0)
    recall_at_5: Mapped[float] = mapped_column(Float, default=0.0)
    recall_at_10: Mapped[float] = mapped_column(Float, default=0.0)
    mrr: Mapped[float] = mapped_column(Float, default=0.0)
    avg_retrieved_tokens: Mapped[float] = mapped_column(Float, default=0.0)
    avg_context_tokens: Mapped[float] = mapped_column(Float, default=0.0)
    avg_retrieval_latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[TS] = mapped_column(server_default=func.now())
