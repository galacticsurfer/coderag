"""Phase 4: relationship extraction + bounded one-hop graph expansion."""

from __future__ import annotations

import pytest
from sqlalchemy import and_, select

from coderag.core.config import Settings
from coderag.db.models import Symbol, SymbolRelationship
from coderag.embeddings.registry import get_embedding_provider
from coderag.parsing.base import CALLS, CONTAINS, IMPORTS, TESTS
from coderag.retrieval.engine import build_engine

pytestmark = pytest.mark.db


def _sym(session, repo_id, qual):
    return session.scalar(
        select(Symbol).where(Symbol.repository_id == repo_id, Symbol.qualified_name == qual)
    )


def _rel_exists(session, repo_id, src_qual, tgt_qual, rtype):
    src = _sym(session, repo_id, src_qual)
    tgt = _sym(session, repo_id, tgt_qual)
    if not src or not tgt:
        return False
    return session.scalar(
        select(SymbolRelationship).where(and_(
            SymbolRelationship.source_symbol_id == src.id,
            SymbolRelationship.target_symbol_id == tgt.id,
            SymbolRelationship.relationship_type == rtype,
        ))
    ) is not None


def test_contains_edges(db_session, demo_repo):
    assert _rel_exists(db_session, demo_repo.id,
                       "payments.payment_service.PaymentService",
                       "payments.payment_service.PaymentService.retry_payment", CONTAINS)


def test_calls_self_resolves(db_session, demo_repo):
    assert _rel_exists(db_session, demo_repo.id,
                       "payments.payment_service.PaymentService.retry_payment",
                       "payments.payment_service.PaymentService.process_payment", CALLS)


def test_import_edge_resolves_in_repo(db_session, demo_repo):
    assert _rel_exists(db_session, demo_repo.id,
                       "payments.payment_service",
                       "payments.retry_policy.RetryPolicy", IMPORTS)


def test_caller_edge_checkout_to_retry(db_session, demo_repo):
    # CheckoutService.handle_failure calls PaymentService.retry_payment
    assert _rel_exists(db_session, demo_repo.id,
                       "payments.checkout_service.CheckoutService.handle_failure",
                       "payments.payment_service.PaymentService.retry_payment", CALLS)


def test_tests_edge_created(db_session, demo_repo):
    # a test_* function that calls a symbol yields a TESTS edge
    n = db_session.scalar(
        select(SymbolRelationship).where(
            SymbolRelationship.repository_id == demo_repo.id,
            SymbolRelationship.relationship_type == TESTS,
        )
    )
    assert n is not None


def test_no_self_loops_and_targets_in_repo(db_session, demo_repo):
    rels = db_session.scalars(
        select(SymbolRelationship).where(SymbolRelationship.repository_id == demo_repo.id)
    ).all()
    assert rels
    for r in rels:
        assert r.source_symbol_id != r.target_symbol_id
        assert r.target_symbol_id is not None


def test_graph_expansion_adds_callers_and_tests(db_session, demo_repo):
    engine = build_engine(embedding_provider=get_embedding_provider(), with_graph=True)
    out = engine.search(db_session, demo_repo.id, "PaymentService.retry_payment", top_n=50)
    reasons = {r for c in out.candidates for r in c.reasons}
    quals = {c.qualified_name for c in out.candidates}
    # callers of retry_payment (checkout) and its tests should be pulled in
    assert any(r.startswith("graph_") for r in reasons)
    assert "payments.checkout_service.CheckoutService.handle_failure" in quals


def test_graph_expansion_is_bounded(db_session, demo_repo):
    settings = Settings(graph_max_candidates=2, graph_max_tokens=100000)
    engine = build_engine(settings=settings,
                          embedding_provider=get_embedding_provider(), with_graph=True)
    base = build_engine(embedding_provider=get_embedding_provider())
    baseline = base.search(db_session, demo_repo.id, "retry_payment", top_n=1000)
    expanded = engine.search(db_session, demo_repo.id, "retry_payment", top_n=1000)
    # expansion never adds more than graph_max_candidates new symbols
    assert len(expanded.candidates) <= len(baseline.candidates) + 2
