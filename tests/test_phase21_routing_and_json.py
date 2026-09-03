"""Model routing, JSON compressor, per-model attribution, detectors R8-R10."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy import select

from coderag import compression as C
from coderag import doctor as D
from coderag.db.models import LLMRequest
from coderag.model_routing import apply_model_route, parse_routes

PIN, POUT = 5.0, 25.0


# ---- routing transform -----------------------------------------------------

def test_parse_routes():
    assert parse_routes(["a=b", " x = y "]) == {"a": "b", "x": "y"}
    for bad in ["nope", "=b", "a=", ""]:
        with pytest.raises(ValueError):
            parse_routes([bad])


def _body(model="claude-opus-4-8") -> bytes:
    return json.dumps({"model": model, "max_tokens": 100,
                       "messages": [{"role": "user", "content": "hi"}]}).encode()


def test_route_rewrites_exact_matches_only():
    routes = {"claude-opus-4-8": "claude-sonnet-5"}
    out = apply_model_route(_body(), routes)
    assert out is not None
    new, requested, target = out
    assert json.loads(new)["model"] == "claude-sonnet-5"
    assert (requested, target) == ("claude-opus-4-8", "claude-sonnet-5")
    # a model the user didn't name is never touched — no prefix matching
    assert apply_model_route(_body("claude-opus-4-8-20991231"), routes) is None
    assert apply_model_route(_body("claude-haiku-4-5"), routes) is None
    # guarded + no-op cases
    assert apply_model_route(b"not json", routes) is None
    assert apply_model_route(_body(), {}) is None
    assert apply_model_route(_body(), {"claude-opus-4-8": "claude-opus-4-8"}) is None
    # deterministic
    assert apply_model_route(_body(), routes) == apply_model_route(_body(), routes)


# ---- proxy wiring: routed model forwarded, requested model recorded --------

def _proxy_client(handler, **app_kw):
    from coderag.proxy import create_app

    app = create_app("https://upstream.example", **app_kw)
    app.state.client = httpx.AsyncClient(
        base_url="https://upstream.example", transport=httpx.MockTransport(handler))
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.local"), app


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.mark.db
def test_proxy_routes_and_records_requested_model(engine, db_session):
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        served = json.loads(request.content)["model"]
        return httpx.Response(200, json={"model": served, "usage": {
            "input_tokens": 10, "output_tokens": 5}})

    async def go(app_kw):
        client, app = _proxy_client(handler, **app_kw)
        async with client:
            await client.post("/v1/messages", content=_body(),
                              headers={"content-type": "application/json"})
        return app

    _run(go({}))
    assert seen[0] == _body()                                  # off: untouched

    app = _run(go({"routes": {"claude-opus-4-8": "claude-sonnet-5"}}))
    assert json.loads(seen[1])["model"] == "claude-sonnet-5"   # upstream sees routed
    assert app.state.route_totals["requests_routed"] == 1

    rows = db_session.scalars(select(LLMRequest).where(
        LLMRequest.provider == "proxy").order_by(LLMRequest.id)).all()
    assert rows[0].requested_model is None                     # not routed
    assert rows[1].requested_model == "claude-opus-4-8"        # original preserved
    assert rows[1].model == "claude-sonnet-5"                  # served model


# ---- JSON content-shape compressor -----------------------------------------

def test_json_compressor_keeps_structure_and_errors(tmp_path):
    payload = {
        "status": "failed",
        "items": [{"id": i, "blob": "x" * 50} for i in range(200)],
        "error": {"message": "quota exceeded on shard 7", "detail": "d" * 900},
        "description": "y" * 2_000,
    }
    text = json.dumps(payload, indent=2)
    out = C.compress_json_text(text, threshold=2000, directory=tmp_path)
    assert out is not None and len(out) < len(text)
    body, _, _ = out.partition("\n[coderag:")
    got = json.loads(body)
    assert set(got) == set(payload)                       # every key survives
    assert got["error"] == payload["error"]               # error subtree intact
    assert "items elided" in json.dumps(got["items"])     # array edges kept
    assert got["items"][0] == payload["items"][0]
    assert got["items"][-1] == payload["items"][-1]
    assert got["description"].startswith("y" * 400)       # long string truncated
    assert "chars elided" in got["description"]
    # recoverable
    key = out.split('coderag_expand("')[1].split('"')[0]
    assert C.load_original(key, tmp_path) == text


def test_json_compressor_declines_non_json_and_small(tmp_path):
    assert C.compress_json_text("plain log line", 2000, tmp_path) is None
    small = json.dumps({"ok": True})
    assert C.compress_json_text(small, 2000, tmp_path) is None


def test_compress_text_routes_json_before_log_pipeline(tmp_path):
    payload = json.dumps({"rows": [{"n": i} for i in range(500)]}, indent=2)
    out = C.compress_text(payload, threshold=2000, keep=800, directory=tmp_path)
    assert "items elided" in out                          # JSON path, not elide_middle
    assert "chars of tool output" not in out


def test_tool_schema_chars_counts_tools_only():
    body = json.dumps({
        "model": "m",
        "tools": [{"name": "bash", "description": "run", "input_schema": {}}],
        "messages": [{"role": "user", "content": "hello"}],
    }).encode()
    n = C.tool_schema_chars_in_body(body)
    assert n == len(json.dumps(
        [{"name": "bash", "description": "run", "input_schema": {}}]))
    assert C.tool_schema_chars_in_body(b"junk") == 0
    assert C.tool_schema_chars_in_body(json.dumps({"model": "m"}).encode()) == 0


# ---- doctor: model mix, routing savings, R8-R10 ----------------------------

@dataclass
class Row:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int | None = 0
    cache_creation_input_tokens: int | None = 0
    tool_result_chars: int = 0
    tool_schema_chars: int = 0
    token_lean_active: bool = False
    model: str = "claude-opus-4-8"
    requested_model: str | None = None
    success: bool = True


def test_model_mix_hand_checked():
    rows = [Row(input_tokens=1_000_000, output_tokens=100_000),
            Row(input_tokens=1_000_000, output_tokens=100_000,
                model="claude-haiku-4-5")]
    mix = D.model_mix(rows, (PIN, POUT))
    assert [m.model for m in mix] == ["claude-opus-4-8", "claude-haiku-4-5"]
    assert mix[0].est_usd == pytest.approx(1 * 5.0 + 0.1 * 25.0)   # $7.50
    assert mix[1].est_usd == pytest.approx(1 * 1.0 + 0.1 * 5.0)    # $1.50


def test_routing_savings_measured():
    rows = [
        Row(input_tokens=1_000_000, output_tokens=100_000,
            model="claude-sonnet-5", requested_model="claude-opus-4-8"),
        Row(input_tokens=1_000_000, output_tokens=100_000),          # not routed
        Row(input_tokens=10, output_tokens=1,
            model="mystery-model", requested_model="claude-opus-4-8"),
    ]
    rt = D.routing_savings(rows)
    assert rt.routed_requests == 2
    assert rt.unpriced_requests == 1
    # opus (5+2.5) - sonnet (3+1.5) = $3.00 on the priced row
    assert rt.saved_usd == pytest.approx((5.0 + 2.5) - (3.0 + 1.5))


def codes(rows) -> set[str]:
    return {d.code for d in D.examine(rows, PIN, POUT, retrieval_queries=50).diagnoses}


def test_r8_expensive_model_dominant():
    rows = [Row(input_tokens=2_000, output_tokens=300,
                cached_input_tokens=30_000) for _ in range(20)]
    ds = D.examine(rows, PIN, POUT, retrieval_queries=50,
                   ordered_total_input=[32_000] * 20).diagnoses
    r8 = next(d for d in ds if d.code == "expensive_model_dominant")
    assert "--route" in r8.action and "claude-opus-4-8" in r8.evidence
    # mixed traffic -> silent
    mixed = rows[:10] + [Row(input_tokens=2_000, output_tokens=300,
                             cached_input_tokens=30_000,
                             model="claude-haiku-4-5") for _ in range(10)]
    assert "expensive_model_dominant" not in codes(mixed)
    # cheap model dominant -> silent
    cheap = [Row(input_tokens=2_000, output_tokens=300, cached_input_tokens=30_000,
                 model="claude-haiku-4-5") for _ in range(20)]
    assert "expensive_model_dominant" not in codes(cheap)


def test_r9_tool_schema_heavy():
    heavy = [Row(input_tokens=5_000, output_tokens=300, cached_input_tokens=30_000,
                 tool_schema_chars=12_000) for _ in range(10)]
    assert "tool_schema_heavy" in codes(heavy)
    light = [Row(input_tokens=5_000, output_tokens=300, cached_input_tokens=30_000,
                 tool_schema_chars=1_000) for _ in range(10)]
    assert "tool_schema_heavy" not in codes(light)


def test_r10_retry_storm():
    stormy = ([Row(input_tokens=1_000, output_tokens=10, cached_input_tokens=30_000,
                   success=False)] * 3
              + [Row(input_tokens=1_000, output_tokens=300,
                     cached_input_tokens=30_000)] * 7)
    assert "retry_storm" in codes(stormy)
    calm = ([Row(input_tokens=1_000, output_tokens=10, cached_input_tokens=30_000,
                 success=False)]
            + [Row(input_tokens=1_000, output_tokens=300,
                   cached_input_tokens=30_000)] * 9)
    assert "retry_storm" not in codes(calm)


@pytest.mark.db
def test_endpoint_exposes_models_and_routing(engine, db_session):
    from fastapi.testclient import TestClient

    from coderag.api.app import app, get_session

    db_session.add(LLMRequest(
        provider="proxy", model="claude-sonnet-5",
        requested_model="claude-opus-4-8",
        input_tokens=1_000_000, output_tokens=100_000, cached_input_tokens=0,
        latency_ms=1, success=True))
    db_session.commit()

    app.dependency_overrides[get_session] = lambda: db_session
    try:
        with TestClient(app) as client:
            data = client.get("/doctor").json()
    finally:
        app.dependency_overrides.pop(get_session)
    assert data["models"][0]["model"] == "claude-sonnet-5"
    assert data["routing"]["routed_requests"] == 1
    assert data["routing"]["saved_usd"] == pytest.approx(3.0)
