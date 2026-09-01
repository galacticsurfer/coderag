"""MCP server tools: real calls against the DB + telemetry lands in the dashboard."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from coderag.db.models import QueryRecord

pytestmark = pytest.mark.db
mcp = pytest.importorskip("mcp")


def _tools():
    from coderag.mcp_server import server

    return {t.name for t in asyncio.run(server.list_tools())}


def test_tools_registered():
    assert {
        "coderag_search", "coderag_context", "coderag_symbol",
        "coderag_repositories", "coderag_index",
    } <= _tools()


def test_search_tool_returns_ranked_symbols(engine, db_session, demo_repo):
    from coderag.mcp_server import coderag_search

    rows = coderag_search("retry failed payment", repository="payments", limit=5)
    assert rows and len(rows) <= 5
    assert any(r["symbol"].endswith("PaymentService.retry_payment") for r in rows)
    first = rows[0]
    assert first["why"] and first["file"] and "-" in first["lines"]


def test_context_tool_returns_budgeted_code(engine, db_session, demo_repo):
    from coderag.mcp_server import coderag_context

    out = coderag_context("why can retry_payment leave an invoice pending?",
                          repository="payments")
    assert "TARGET SYMBOL" in out["context"]
    acct = out["token_accounting"]
    assert acct["context_tokens"] <= acct["candidate_tokens"]
    assert out["selected_symbols"]


def test_symbol_tool_fetches_source(engine, db_session, demo_repo):
    from coderag.mcp_server import coderag_symbol

    out = coderag_symbol("PaymentService.retry_payment", repository="payments")
    assert "def retry_payment" in out["source"]
    assert out["symbol"].endswith("PaymentService.retry_payment")


def test_repositories_tool(engine, db_session, demo_repo):
    from coderag.mcp_server import coderag_repositories

    names = {r["name"] for r in coderag_repositories()}
    assert "payments" in names


def test_mcp_queries_appear_in_dashboard(engine, db_session, demo_repo):
    """MCP usage is recorded, so /queries and /dashboard reflect MCP-driven savings."""
    from coderag.api.app import app
    from coderag.mcp_server import coderag_context

    coderag_context("retry failed payment", repository="payments")
    db_session.commit()

    recorded = db_session.scalars(select(QueryRecord)).all()
    assert any(q.mode == "context" and q.candidate_tokens > 0 for q in recorded)

    client = TestClient(app)
    rows = client.get("/queries?limit=20").json()
    assert rows and any(r["tokens_saved"] >= 0 for r in rows)
    m = client.get("/metrics").json()
    assert m["queries"] >= 1


def test_default_repository_setting_resolves(engine, db_session, demo_repo, monkeypatch):
    """CODERAG_DEFAULT_REPOSITORY lets MCP tools omit the repository argument."""
    from coderag.core.config import Settings, get_settings
    from coderag.service import resolve_repository

    get_settings.cache_clear()
    monkeypatch.setenv("CODERAG_DEFAULT_REPOSITORY", "payments")
    try:
        assert Settings().default_repository == "payments"
        assert resolve_repository(db_session, None).name == "payments"
    finally:
        get_settings.cache_clear()
