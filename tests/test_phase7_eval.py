"""Phase 7: evaluation harness — Recall@K, MRR, baseline token comparison."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from coderag.db.models import EvaluationRun
from coderag.evaluation.datasets import DEMO_DATASET, load_dataset
from coderag.evaluation.harness import (
    _first_hit_rank,
    _matches,
    benchmark_latency,
    compare_baseline,
    evaluate_retrieval,
    persist_eval_run,
)

pytestmark = pytest.mark.db


def test_matches_helper():
    assert _matches("payments.a.b.retry_payment", "retry_payment")
    assert _matches("payments.retry_policy", "payments.retry_policy")
    assert not _matches("payments.a.foo", "retry_payment")


def test_first_hit_rank():
    quals = ["a.x", "a.retry", "a.y"]
    assert _first_hit_rank(quals, ["retry"]) == 2
    assert _first_hit_rank(quals, ["nope"]) is None


def test_demo_dataset_loads():
    cases = load_dataset(DEMO_DATASET)
    assert len(cases) >= 6
    assert all(c.question and c.expected_symbols for c in cases)


def test_retrieval_metrics_on_demo(db_session, demo_repo):
    cases = load_dataset(DEMO_DATASET)
    m = evaluate_retrieval(db_session, demo_repo, cases)
    assert m.n_cases == len(cases)
    # hybrid retrieval should find the target within top-10 for most questions
    assert m.recall_at[10] >= 0.75
    assert m.recall_at[10] >= m.recall_at[1]
    assert m.mrr > 0.0
    assert m.avg_candidate_tokens > 0 and m.avg_context_tokens > 0


def test_baseline_comparison_shows_savings(db_session, demo_repo):
    cases = load_dataset(DEMO_DATASET)
    cmp = compare_baseline(db_session, demo_repo, cases)
    # vs dumping the whole repository, budgeted context is smaller
    assert cmp.avg_baseline_tokens > cmp.avg_rag_context_tokens
    assert cmp.token_reduction_percent > 0.0
    assert cmp.baseline_kind == "whole_repository"


def test_persist_eval_run(db_session, demo_repo):
    cases = load_dataset(DEMO_DATASET)
    m = evaluate_retrieval(db_session, demo_repo, cases)
    run = persist_eval_run(db_session, "t", DEMO_DATASET, m)
    db_session.commit()
    stored = db_session.scalar(select(EvaluationRun).where(EvaluationRun.id == run.id))
    assert stored is not None
    assert 0.0 <= stored.recall_at_10 <= 1.0
    assert stored.config["rrf_k"] > 0


def test_latency_benchmark(db_session, demo_repo):
    cases = load_dataset(DEMO_DATASET)
    lat = benchmark_latency(db_session, demo_repo, cases, repeats=2)
    assert lat.n == len(cases) * 2
    assert lat.p95_ms >= lat.p50_ms >= 0.0
