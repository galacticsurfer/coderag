"""Dashboard + telemetry API: /metrics savings, /queries, /dashboard page."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from coderag.api.app import app
from tests.conftest import DEMO_REPO_PATH

pytestmark = pytest.mark.db
client = TestClient(app)


def _index():
    r = client.post("/repositories", json={"name": "payments", "path": DEMO_REPO_PATH})
    client.post(f"/repositories/{r.json()['id']}/index")


def test_metrics_savings_fields(engine, db_session):
    _index()
    client.post("/context", json={"query": "why can retry leave invoice pending?",
                                  "repository": "payments"})
    m = client.get("/metrics").json()
    for k in ("total_candidate_tokens", "total_context_tokens", "total_tokens_saved",
              "avg_token_reduction_percent"):
        assert k in m
    assert m["total_candidate_tokens"] >= m["total_context_tokens"]
    assert m["total_tokens_saved"] == m["total_candidate_tokens"] - m["total_context_tokens"]


def test_queries_endpoint_reports_savings(engine, db_session):
    _index()
    client.post("/search", json={"query": "retry_payment", "repository": "payments"})
    client.post("/context", json={"query": "retry failed payment", "repository": "payments"})
    rows = client.get("/queries?limit=50").json()
    assert len(rows) >= 2
    modes = {r["mode"] for r in rows}
    assert {"search", "context"} <= modes
    ctx = next(r for r in rows if r["mode"] == "context")
    assert ctx["tokens_saved"] == ctx["candidate_tokens"] - ctx["context_tokens"]
    assert 0.0 <= ctx["reduction_percent"] <= 100.0
    assert ctx["query"]  # query text recorded


def test_dashboard_html_served(engine, db_session):
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "Token Dashboard" in body
    assert "queries?limit" in body and "metrics" in body  # fetches the JSON endpoints
