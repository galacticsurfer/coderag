"""Phase 2: exact symbol + lexical retrieval, fusion, and repository isolation."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from coderag.db.models import Symbol
from coderag.retrieval.base import EXACT_SYMBOL, LEXICAL
from coderag.retrieval.engine import build_engine

pytestmark = pytest.mark.db


def _search(session, repo_id, query, n=10):
    return build_engine().search(session, repo_id, query, top_n=n).candidates


def test_exact_qualified_symbol_ranks_first(db_session, demo_repo):
    cands = _search(db_session, demo_repo.id, "PaymentService.retry_payment")
    assert cands
    assert cands[0].qualified_name.endswith("PaymentService.retry_payment")
    assert EXACT_SYMBOL in cands[0].reasons


def test_bare_symbol_name(db_session, demo_repo):
    cands = _search(db_session, demo_repo.id, "calculate_timeout")
    quals = [c.qualified_name for c in cands]
    assert any(q.endswith("RetryPolicy.calculate_timeout") for q in quals)


def test_lexical_finds_constant_identifier(db_session, demo_repo):
    cands = _search(db_session, demo_repo.id, "PAYMENT_RETRY_LIMIT")
    quals = [c.qualified_name for c in cands]
    assert any(q.startswith("payments.retry_policy") for q in quals)
    # this is a lexical hit, not an exact symbol match
    hit = next(c for c in cands if q_startswith(c, "payments.retry_policy"))
    assert LEXICAL in hit.reasons


def test_lexical_finds_error_code(db_session, demo_repo):
    cands = _search(db_session, demo_repo.id, "ERR_PAYMENT_102")
    assert any(c.qualified_name.startswith("payments.retry_policy") for c in cands)


def test_natural_language_query_matches_retry(db_session, demo_repo):
    cands = _search(db_session, demo_repo.id, "retry failed payment")
    quals = [c.qualified_name for c in cands]
    assert any(q.endswith("PaymentService.retry_payment") for q in quals)


def test_fusion_merges_reasons(db_session, demo_repo):
    # 'retry_payment' is both a symbol name and lexically present.
    cands = _search(db_session, demo_repo.id, "retry_payment")
    hit = next(c for c in cands if c.qualified_name.endswith("PaymentService.retry_payment"))
    assert EXACT_SYMBOL in hit.reasons
    assert LEXICAL in hit.reasons
    assert hit.fused_score > 0


def test_no_results_for_nonsense(db_session, demo_repo):
    cands = _search(db_session, demo_repo.id, "zzxqjunknonexistenttoken")
    assert cands == []


def test_repository_isolation(db_session, demo_repo):
    from coderag.indexing.indexer import index_repository
    from tests.conftest import DEMO_REPO_PATH

    other, *_ = index_repository(db_session, "payments_copy", DEMO_REPO_PATH)
    db_session.commit()
    assert other.id != demo_repo.id

    cands = _search(db_session, demo_repo.id, "retry_payment")
    ids = [c.symbol_id for c in cands]
    owners = set(
        db_session.scalars(select(Symbol.repository_id).where(Symbol.id.in_(ids)))
    )
    assert owners == {demo_repo.id}  # nothing leaked from payments_copy


def q_startswith(candidate, prefix: str) -> bool:
    return candidate.qualified_name.startswith(prefix)
