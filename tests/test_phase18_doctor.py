"""Cost doctor: attribution math, diagnosis rules, proxy capture, endpoint."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy import select

from coderag import doctor as D
from coderag.db.models import LLMRequest

PIN, POUT = 5.0, 25.0  # $/Mtok used throughout — hand-checkable numbers


@dataclass
class Row:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int | None = 0
    cache_creation_input_tokens: int | None = 0
    tool_result_chars: int = 0


def healthy(n: int = 20) -> list[Row]:
    """Well-behaved traffic: cached, modest output, no tool bloat, flat growth."""
    return [Row(input_tokens=1_000, output_tokens=300,
                cached_input_tokens=30_000, cache_creation_input_tokens=500)
            for _ in range(n)]


# ---- attribution: pure arithmetic, hand-checked ----------------------------

def test_attribution_math_hand_checked():
    rows = [
        Row(input_tokens=1_000_000, output_tokens=100_000,
            cached_input_tokens=2_000_000, cache_creation_input_tokens=400_000,
            tool_result_chars=8_000),
        Row(input_tokens=500_000, output_tokens=0,
            cached_input_tokens=None, cache_creation_input_tokens=None),
    ]
    b = D.attribute(rows, PIN, POUT)
    assert b.requests == 2
    assert b.fresh_input_usd == pytest.approx(1.5 * 5.0)          # 1.5M @ $5/M
    assert b.cache_read_usd == pytest.approx(2.0 * 5.0 * 0.10)    # 2M @ 0.1x
    assert b.cache_write_usd == pytest.approx(0.4 * 5.0 * 1.25)   # 0.4M @ 1.25x
    assert b.output_usd == pytest.approx(0.1 * 25.0)              # 0.1M @ $25/M
    assert b.total_usd == pytest.approx(7.5 + 1.0 + 2.5 + 2.5)
    assert b.cache_hit_rate == pytest.approx(2.0 / 3.5)
    assert b.tool_result_chars == 8_000


def test_empty_traffic_yields_empty_report():
    r = D.examine([], PIN, POUT)
    assert r.breakdown.requests == 0
    assert r.breakdown.total_usd == 0.0
    assert r.diagnoses == []


# ---- each rule fires on crafted data and stays silent on healthy data ------

def codes(rows, **kw) -> set[str]:
    return {d.code for d in D.examine(rows, PIN, POUT, **kw).diagnoses}


def test_healthy_traffic_gets_no_diagnosis():
    assert codes(healthy(), retrieval_queries=50,
                 ordered_total_input=[31_000] * 20) == set()


def test_r1_output_dominant():
    rows = [Row(input_tokens=10_000, output_tokens=50_000)]  # output = 96% of cost
    got = D.examine(rows, PIN, POUT).diagnoses
    assert [d.code for d in got][0] == "output_dominant"
    # est = 25% of output spend: 50k tokens * $25/M * 0.25
    assert got[0].est_saving_usd == pytest.approx(0.05 * 25 * 0.25, abs=1e-4)
    assert "output_dominant" not in codes(healthy())


def test_r2_cache_misses():
    rows = [Row(input_tokens=20_000, output_tokens=100, cached_input_tokens=0)
            for _ in range(6)]
    assert "cache_misses" in codes(rows)
    # too few requests -> silent
    assert "cache_misses" not in codes(rows[:4])
    # small requests -> silent even with zero hits
    assert "cache_misses" not in codes(
        [Row(input_tokens=2_000, output_tokens=100) for _ in range(6)])


def test_r3_history_growth():
    growing = [5_000 * (i + 1) for i in range(12)]         # 5k -> 60k
    assert "history_growth" in codes(healthy(12), retrieval_queries=50,
                                     ordered_total_input=growing)
    flat = [30_000] * 12
    assert "history_growth" not in codes(healthy(12), retrieval_queries=50,
                                         ordered_total_input=flat)
    assert "history_growth" not in codes(healthy(4), retrieval_queries=50,
                                         ordered_total_input=growing[:4])  # < 8 rows


def test_r4_retrieval_unused():
    assert "retrieval_unused" in codes(healthy(12), retrieval_queries=0,
                                       ordered_total_input=[31_000] * 12)
    assert "retrieval_unused" not in codes(healthy(12), retrieval_queries=30,
                                           ordered_total_input=[31_000] * 12)


def test_r5_tool_output_heavy():
    rows = [Row(input_tokens=10_000, output_tokens=100,
                cached_input_tokens=30_000, tool_result_chars=20_000)]  # ~5k tok = 50%
    got = [d for d in D.examine(rows, PIN, POUT).diagnoses
           if d.code == "tool_output_heavy"]
    assert got and "--compress" in got[0].action
    quiet = [Row(input_tokens=10_000, output_tokens=100,
                 cached_input_tokens=30_000, tool_result_chars=1_000)]
    assert "tool_output_heavy" not in {d.code for d in
                                       D.examine(quiet, PIN, POUT).diagnoses}


def test_diagnoses_ranked_by_estimated_saving_unquantified_last():
    rows = [Row(input_tokens=20_000, output_tokens=60_000, cached_input_tokens=0,
                tool_result_chars=40_000) for _ in range(12)]
    ds = D.examine(rows, PIN, POUT, retrieval_queries=0,
                   ordered_total_input=[3_000 * (i + 1) for i in range(12)]).diagnoses
    savings = [d.est_saving_usd for d in ds]
    quantified = [s for s in savings if s is not None]
    assert quantified == sorted(quantified, reverse=True)
    assert all(s is None for s in savings[len(quantified):])
    # every diagnosis states its assumption and cites evidence with numbers
    assert all(d.assumption and any(c.isdigit() for c in d.evidence) for d in ds)


# ---- proxy records the new columns (JSON + SSE paths) ----------------------

TOOL_BODY = json.dumps({
    "model": "claude-opus-4-8",
    "messages": [
        {"role": "user", "content": "run it"},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "x" * 5_000},
        ]},
    ],
}).encode()

SSE_CACHE_BODY = (
    'data: {"type":"message_start","message":{"model":"claude-opus-4-8",'
    '"usage":{"input_tokens":1200,"cache_read_input_tokens":800,'
    '"cache_creation_input_tokens":250}}}\n\n'
    'data: {"type":"message_delta","usage":{"output_tokens":42}}\n\n'
    "data: [DONE]\n\n"
)


def _proxy_client(handler) -> httpx.AsyncClient:
    from coderag.proxy import create_app

    app = create_app("https://upstream.example")
    app.state.client = httpx.AsyncClient(
        base_url="https://upstream.example", transport=httpx.MockTransport(handler)
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.local"
    )


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.mark.db
def test_proxy_records_cache_creation_and_tool_chars_json(engine, db_session):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "model": "claude-opus-4-8",
            "usage": {"input_tokens": 321, "output_tokens": 7,
                      "cache_read_input_tokens": 100,
                      "cache_creation_input_tokens": 55},
        })

    async def go():
        async with _proxy_client(handler) as client:
            return await client.post("/v1/messages", content=TOOL_BODY,
                                     headers={"content-type": "application/json"})

    assert _run(go()).status_code == 200
    r = db_session.scalar(select(LLMRequest).where(LLMRequest.provider == "proxy"))
    assert r is not None
    assert r.cache_creation_input_tokens == 55
    assert r.tool_result_chars == 5_000


@pytest.mark.db
def test_proxy_records_cache_creation_sse(engine, db_session):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=SSE_CACHE_BODY.encode(),
                              headers={"content-type": "text/event-stream"})

    async def go():
        async with _proxy_client(handler) as client:
            return await client.post("/v1/messages", json={"stream": True})

    resp = _run(go())
    assert resp.status_code == 200
    assert resp.content == SSE_CACHE_BODY.encode()   # tee stays byte-identical
    r = db_session.scalar(select(LLMRequest).where(LLMRequest.provider == "proxy"))
    assert r is not None
    assert (r.input_tokens, r.cached_input_tokens,
            r.cache_creation_input_tokens, r.output_tokens) == (1200, 800, 250, 42)


# ---- examine_from_db + API endpoint ----------------------------------------

@pytest.mark.db
def test_examine_from_db_and_endpoint(engine, db_session):
    from fastapi.testclient import TestClient

    from coderag.api.app import app, get_session

    for _i in range(12):
        db_session.add(LLMRequest(
            provider="proxy", model="claude-opus-4-8",
            input_tokens=20_000, output_tokens=60_000,
            cached_input_tokens=0, cache_creation_input_tokens=0,
            tool_result_chars=0, latency_ms=1, success=True,
        ))
    db_session.commit()

    report = D.examine_from_db(db_session, PIN, POUT)
    assert report.breakdown.requests == 12
    assert {d.code for d in report.diagnoses} >= {"output_dominant",
                                                  "cache_misses",
                                                  "retrieval_unused"}

    app.dependency_overrides[get_session] = lambda: db_session
    try:
        with TestClient(app) as client:
            data = client.get("/doctor").json()
    finally:
        app.dependency_overrides.pop(get_session)
    assert data["breakdown"]["requests"] == 12
    assert data["breakdown"]["total_usd"] > 0
    codes_out = [d["code"] for d in data["diagnoses"]]
    assert "output_dominant" in codes_out
    assert all({"title", "evidence", "action", "assumption"} <= set(d)
               for d in data["diagnoses"])
    assert "not billing data" in data["note"]


# ---- CLI smoke -------------------------------------------------------------

@pytest.mark.db
def test_cli_doctor_smoke(engine, db_session, monkeypatch):
    from typer.testing import CliRunner

    from coderag.cli.main import app as cli_app

    db_session.add(LLMRequest(
        provider="proxy", model="claude-opus-4-8",
        input_tokens=10_000, output_tokens=50_000,
        cached_input_tokens=0, latency_ms=1, success=True,
    ))
    db_session.commit()

    result = CliRunner().invoke(cli_app, ["doctor"])
    assert result.exit_code == 0
    assert "Where the money went" in result.output
    assert "Output tokens dominate" in result.output
    assert "not billing data" in result.output


@pytest.mark.db
def test_cli_doctor_no_traffic(engine, db_session):
    from typer.testing import CliRunner

    from coderag.cli.main import app as cli_app

    result = CliRunner().invoke(cli_app, ["doctor"])
    assert result.exit_code == 0
    assert "No observed LLM traffic yet" in result.output
