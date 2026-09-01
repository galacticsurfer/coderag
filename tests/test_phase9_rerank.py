"""Phase 9: optional deterministic reranker."""

from __future__ import annotations

import pytest

from coderag.embeddings.registry import get_embedding_provider
from coderag.evaluation.datasets import DEMO_DATASET, load_dataset
from coderag.evaluation.harness import evaluate_retrieval
from coderag.retrieval.engine import build_engine
from coderag.retrieval.reranking import DeterministicReranker

pytestmark = pytest.mark.db


def test_deterministic_reranker_is_stable_and_scores(db_session, demo_repo):
    engine = build_engine(embedding_provider=get_embedding_provider(), with_graph=True)
    out = engine.search(db_session, demo_repo.id, "retry failed payment", top_n=None)
    rr = DeterministicReranker()
    first = rr.rerank(db_session, demo_repo.id, "retry failed payment", list(out.candidates))
    second = rr.rerank(db_session, demo_repo.id, "retry failed payment", list(out.candidates))
    assert [c.symbol_id for c in first] == [c.symbol_id for c in second]  # deterministic
    assert all(c.rerank_score is not None for c in first)


def test_engine_with_reranker_orders_by_rerank_score(db_session, demo_repo):
    engine = build_engine(
        embedding_provider=get_embedding_provider(), with_graph=True, with_reranker=True
    )
    out = engine.search(db_session, demo_repo.id, "PaymentService.retry_payment", top_n=10)
    assert out.candidates
    assert out.candidates[0].rerank_score is not None
    scores = [c.rerank_score for c in out.candidates]
    assert scores == sorted(scores, reverse=True)  # ordered by rerank score
    assert out.candidates[0].qualified_name.endswith("PaymentService.retry_payment")


def test_eval_with_rerank_runs(db_session, demo_repo):
    cases = load_dataset(DEMO_DATASET)
    m = evaluate_retrieval(db_session, demo_repo, cases, rerank=True)
    assert m.n_cases == len(cases)
    assert m.recall_at[10] >= 0.75
