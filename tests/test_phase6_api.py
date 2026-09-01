"""Phase 6: FastAPI endpoints (search/context/ask/symbols/metrics)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from coderag.api.app import app
from tests.conftest import DEMO_REPO_PATH

pytestmark = pytest.mark.db
client = TestClient(app)


def _register_and_index(engine):
    r = client.post("/repositories", json={"name": "payments", "path": DEMO_REPO_PATH})
    assert r.status_code == 200, r.text
    repo_id = r.json()["id"]
    r = client.post(f"/repositories/{repo_id}/index")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success"
    assert r.json()["symbols_indexed"] > 0
    return repo_id


def test_index_status_and_search(engine, db_session):
    repo_id = _register_and_index(engine)

    r = client.get(f"/repositories/{repo_id}/index/status")
    assert r.status_code == 200 and r.json()["status"] == "success"

    r = client.post("/search", json={"query": "retry failed payment", "repository": "payments"})
    assert r.status_code == 200
    quals = [c["qualified_name"] for c in r.json()["candidates"]]
    assert any(q.endswith("PaymentService.retry_payment") for q in quals)
    assert all(c["reasons"] for c in r.json()["candidates"])


def test_context_endpoint_reports_accounting(engine, db_session):
    _register_and_index(engine)
    r = client.post("/context", json={
        "query": "why can retry_payment leave an invoice pending?", "repository": "payments",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["entries"]
    assert body["accounting"]["candidate_tokens"] >= body["accounting"]["context_tokens"]
    assert "TARGET SYMBOL" in body["prompt"]


def test_symbol_and_relationships(engine, db_session):
    _register_and_index(engine)
    r = client.post("/search", json={"query": "PaymentService.retry_payment",
                                     "repository": "payments", "limit": 1})
    sid = r.json()["candidates"][0]["symbol_id"]

    r = client.get(f"/symbols/{sid}")
    assert r.status_code == 200 and r.json()["source_code"]

    r = client.get(f"/symbols/{sid}/relationships")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_metrics(engine, db_session):
    _register_and_index(engine)
    client.post("/search", json={"query": "retry", "repository": "payments"})
    r = client.get("/metrics")
    assert r.status_code == 200
    m = r.json()
    assert m["repositories"] >= 1 and m["symbols"] > 0 and m["embeddings"] > 0
    assert m["queries"] >= 1


def test_ask_without_llm_returns_503(engine, db_session):
    _register_and_index(engine)
    r = client.post("/ask", json={"query": "why pending?", "repository": "payments"})
    assert r.status_code == 503  # no LLM configured


def test_unknown_repository_404(engine, db_session):
    r = client.post("/search", json={"query": "x", "repository": "nope"})
    assert r.status_code == 404
