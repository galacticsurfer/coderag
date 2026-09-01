"""--measure mode: whole-file baseline vs the budgeted context."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from coderag.api.app import app
from coderag.core.config import Settings
from coderag.db.models import QueryRecord, SourceFile
from coderag.service import run_context

pytestmark = pytest.mark.db


def test_index_records_file_token_counts(db_session, demo_repo):
    files = db_session.scalars(
        select(SourceFile).where(SourceFile.repository_id == demo_repo.id)
    ).all()
    assert files
    assert all(f.token_count > 0 for f in files)


def test_context_reports_whole_file_baseline(db_session, demo_repo):
    _repo, pkg, _ = run_context(
        db_session, "why can retry_payment leave an invoice pending?", "payments",
    )
    a = pkg.accounting
    assert a.baseline_files > 0
    assert a.baseline_tokens > 0
    # the baseline must cover at least the code we actually selected
    assert a.baseline_tokens >= a.context_tokens
    assert a.tokens_saved_vs_files == a.baseline_tokens - a.context_tokens
    assert 0.0 <= a.reduction_vs_files <= 100.0
    d = a.as_dict()
    assert d["baseline_tokens"] == a.baseline_tokens
    assert d["reduction_vs_files"] == a.reduction_vs_files


def test_tight_budget_increases_savings_vs_files(db_session, demo_repo):
    """A smaller budget sends less code, so the saving vs whole files grows."""
    _r, wide, _ = run_context(
        db_session, "retry failed payment", "payments",
        settings=Settings(max_context_tokens=12000, context_overhead_tokens=0),
    )
    _r2, tight, _ = run_context(
        db_session, "retry failed payment", "payments",
        settings=Settings(max_context_tokens=300, context_overhead_tokens=0),
    )
    assert tight.accounting.context_tokens < wide.accounting.context_tokens


def test_baseline_persisted_and_exposed_by_api(db_session, demo_repo):
    run_context(db_session, "retry failed payment", "payments")
    db_session.commit()

    rec = db_session.scalar(select(QueryRecord).where(QueryRecord.mode == "context"))
    assert rec.baseline_tokens > 0 and rec.baseline_files > 0

    client = TestClient(app)
    row = next(r for r in client.get("/queries?limit=20").json() if r["baseline_tokens"])
    assert row["saved_vs_files"] == row["baseline_tokens"] - row["context_tokens"]
    m = client.get("/metrics").json()
    assert m["total_baseline_tokens"] > 0
    assert m["total_saved_vs_files"] == m["total_baseline_tokens"] - sum(
        r["context_tokens"] for r in client.get("/queries?limit=200").json()
        if r["baseline_tokens"]
    )
    assert 0.0 <= m["reduction_vs_files_percent"] <= 100.0


def test_mcp_context_includes_measurement(db_session, demo_repo):
    pytest.importorskip("mcp")
    from coderag.mcp_server import coderag_context

    out = coderag_context("retry failed payment", repository="payments")
    assert "fewer input tokens" in out["measurement"]
    assert out["token_accounting"]["baseline_tokens"] > 0
