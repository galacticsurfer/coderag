"""Phase 5: end-to-end context build on the demo repo (DB) + telemetry."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from coderag.core.config import Settings
from coderag.db.models import QueryRecord, RetrievalResult
from coderag.service import run_context

pytestmark = pytest.mark.db


def test_context_targets_retry_payment_first(db_session, demo_repo):
    _repo, pkg, _ = run_context(
        db_session, "why can PaymentService.retry_payment leave an invoice pending?",
        "payments", settings=Settings(),
    )
    sections = pkg.sections()
    assert sections[0][0] == "TARGET SYMBOL"
    target_syms = [e.candidate.qualified_name for e in sections[0][1]]
    assert any(q.endswith("PaymentService.retry_payment") for q in target_syms)


def test_context_respects_budget_and_drops(db_session, demo_repo):
    _repo, pkg, _ = run_context(
        db_session, "retry failed payment", "payments",
        settings=Settings(context_overhead_tokens=0), max_tokens=120,
    )
    assert pkg.accounting.context_tokens <= 120
    assert pkg.accounting.candidates_selected <= pkg.accounting.candidates_found
    assert pkg.accounting.dropped_tokens >= 0


def test_context_dedup_no_overlap(db_session, demo_repo):
    _repo, pkg, _ = run_context(db_session, "PaymentService", "payments")
    entries = pkg.entries
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            a, b = entries[i].candidate, entries[j].candidate
            assert not (
                a.file_path == b.file_path
                and not (a.end_line < b.start_line or b.end_line < a.start_line)
            )


def test_context_records_telemetry(db_session, demo_repo):
    run_context(db_session, "retry failed payment", "payments")
    db_session.commit()
    q = db_session.scalar(
        select(QueryRecord).where(QueryRecord.mode == "context")
    )
    assert q is not None and q.candidate_tokens > 0
    n_results = db_session.scalar(
        select(func.count()).select_from(RetrievalResult).where(
            RetrievalResult.query_id == q.id
        )
    )
    assert n_results > 0
    n_selected = db_session.scalar(
        select(func.count()).select_from(RetrievalResult).where(
            RetrievalResult.query_id == q.id, RetrievalResult.selected.is_(True)
        )
    )
    assert n_selected == q.candidates_selected


def test_context_token_reduction_reported(db_session, demo_repo):
    _repo, pkg, _ = run_context(
        db_session, "retry failed payment", "payments",
        settings=Settings(max_context_tokens=400, context_overhead_tokens=0),
    )
    acct = pkg.accounting
    # with a tight budget vs many candidates, we expect a real reduction
    assert acct.candidate_tokens >= acct.context_tokens
