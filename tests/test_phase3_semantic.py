"""Phase 3: embedding pipeline caching + semantic/hybrid retrieval."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from coderag.db.models import Symbol, SymbolEmbedding
from coderag.embeddings.registry import get_embedding_provider
from coderag.retrieval.base import SEMANTIC
from coderag.retrieval.engine import build_engine

pytestmark = pytest.mark.db


def test_indexing_creates_one_embedding_per_symbol(db_session, demo_repo):
    n_symbols = db_session.scalar(
        select(func.count()).select_from(Symbol).where(Symbol.repository_id == demo_repo.id)
    )
    n_emb = db_session.scalar(
        select(func.count()).select_from(SymbolEmbedding).where(
            SymbolEmbedding.repository_id == demo_repo.id
        )
    )
    assert n_symbols > 0
    assert n_emb == n_symbols


def test_pipeline_is_cached_on_second_run(db_session, demo_repo):
    from coderag.embeddings.pipeline import EmbeddingPipeline

    provider = get_embedding_provider()
    pipeline = EmbeddingPipeline(db_session, provider)
    # already embedded during indexing -> nothing stale
    assert pipeline.embed_repository(demo_repo) == 0


def test_pipeline_reembeds_on_source_change(db_session, demo_repo):
    from coderag.embeddings.pipeline import EmbeddingPipeline

    sym = db_session.scalar(
        select(Symbol).where(Symbol.qualified_name == "payments.retry_policy.RetryPolicy")
    )
    sym.source_hash = "changed-hash-000"
    db_session.flush()
    provider = get_embedding_provider()
    assert EmbeddingPipeline(db_session, provider).embed_repository(demo_repo) == 1
    # the stored embedding row now tracks the new hash
    row = db_session.scalar(
        select(SymbolEmbedding).where(SymbolEmbedding.symbol_id == sym.id)
    )
    assert row.source_hash == "changed-hash-000"


def test_semantic_only_retrieval_returns_hits(db_session, demo_repo):
    from coderag.retrieval.semantic import SemanticRetriever

    engine = build_engine()
    engine.retrievers = [SemanticRetriever(get_embedding_provider())]
    out = engine.search(db_session, demo_repo.id, "retry failed transactions", top_n=10)
    assert out.candidates
    assert all(SEMANTIC in c.reasons for c in out.candidates)


def test_hybrid_includes_semantic_reason(db_session, demo_repo):
    engine = build_engine(embedding_provider=get_embedding_provider())
    out = engine.search(db_session, demo_repo.id, "retry failed payment", top_n=10)
    quals = [c.qualified_name for c in out.candidates]
    assert any(q.endswith("PaymentService.retry_payment") for q in quals)
    assert any(SEMANTIC in c.reasons for c in out.candidates)


def test_semantic_respects_repository_scope(db_session, demo_repo):
    from coderag.indexing.indexer import index_repository
    from coderag.retrieval.semantic import SemanticRetriever
    from tests.conftest import DEMO_REPO_PATH

    other, *_ = index_repository(db_session, "payments_copy2", DEMO_REPO_PATH)
    db_session.commit()
    hits = SemanticRetriever(get_embedding_provider()).retrieve(
        db_session, demo_repo.id, "retry failed payment", 20
    )
    owners = set(
        db_session.scalars(
            select(Symbol.repository_id).where(Symbol.id.in_([h.symbol_id for h in hits]))
        )
    )
    assert owners == {demo_repo.id}
