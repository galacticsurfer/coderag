"""Output levers: opt-in caps (mechanical) + measured /token-lean effect."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy import select

from coderag import doctor as D
from coderag.db.models import LLMRequest
from coderag.output_caps import apply_output_caps

PIN, POUT = 5.0, 25.0


# ---- apply_output_caps: pure, clamp-down-only, guarded ---------------------

def _req(**over) -> bytes:
    base: dict = {"model": "claude-opus-4-8", "max_tokens": 32_000,
                  "messages": [{"role": "user", "content": "hi"}]}
    base.update(over)
    return json.dumps(base).encode()


def test_caps_clamp_max_tokens_downward_only():
    out = apply_output_caps(_req(), max_tokens_cap=8_000)
    assert out is not None
    assert json.loads(out)["max_tokens"] == 8_000
    # already below the cap -> untouched (None = forward original bytes)
    assert apply_output_caps(_req(max_tokens=500), max_tokens_cap=8_000) is None
    # equal to the cap -> untouched
    assert apply_output_caps(_req(max_tokens=8_000), max_tokens_cap=8_000) is None
    # missing field is never invented
    body = json.dumps({"model": "m", "messages": []}).encode()
    assert apply_output_caps(body, max_tokens_cap=8_000) is None


def test_caps_clamp_thinking_budget_but_not_adaptive():
    body = _req(thinking={"type": "enabled", "budget_tokens": 50_000})
    out = apply_output_caps(body, thinking_budget_cap=10_000)
    assert out is not None
    got = json.loads(out)["thinking"]
    assert got == {"type": "enabled", "budget_tokens": 10_000}
    # adaptive thinking has no budget and must not be touched
    adaptive = _req(thinking={"type": "adaptive"})
    assert apply_output_caps(adaptive, thinking_budget_cap=10_000) is None


def test_caps_guarded_and_deterministic():
    assert apply_output_caps(b"not json", max_tokens_cap=100) is None
    assert apply_output_caps(_req()) is None                 # no caps configured
    a = apply_output_caps(_req(), max_tokens_cap=1_000)
    b = apply_output_caps(_req(), max_tokens_cap=1_000)
    assert a == b                                            # pure function
    # everything except the clamped field survives byte-exactly after reparse
    got, orig = json.loads(a), json.loads(_req())
    orig["max_tokens"] = 1_000
    assert got == orig


# ---- proxy applies caps only when configured -------------------------------

def _proxy_client(handler, **app_kw) -> tuple[httpx.AsyncClient, object]:
    from coderag.proxy import create_app

    app = create_app("https://upstream.example", **app_kw)
    app.state.client = httpx.AsyncClient(
        base_url="https://upstream.example", transport=httpx.MockTransport(handler)
    )
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.local"
    )
    return client, app


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"model": "m", "usage": {
        "input_tokens": 10, "output_tokens": 5}})


@pytest.mark.db
def test_proxy_caps_off_by_default_on_when_flagged(engine, db_session):
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return _ok(request)

    async def go(app_kw):
        client, app = _proxy_client(handler, **app_kw)
        async with client:
            resp = await client.post("/v1/messages", content=_req(),
                                     headers={"content-type": "application/json"})
        return resp, app

    resp, app = _run(go({}))
    assert resp.status_code == 200
    assert seen[0] == _req()                                 # default: byte-identical

    resp, app = _run(go({"cap_output": 4_000, "cap_thinking": 5_000}))
    assert resp.status_code == 200
    assert json.loads(seen[1])["max_tokens"] == 4_000        # flagged: clamped
    assert app.state.cap_totals["requests_capped"] == 1

    async def health(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://proxy.local") as c:
            return (await c.get("/coderag-proxy/health")).json()

    h = _run(health(app))
    assert (h["cap_output"], h["cap_thinking"]) == (4_000, 5_000)
    assert h["caps"] == {"requests_capped": 1}


# ---- proxy flags whether /token-lean is active (no content stored) ---------

@pytest.mark.db
def test_proxy_records_token_lean_flag(engine, db_session):
    async def go(body: bytes):
        client, _app = _proxy_client(_ok)
        async with client:
            return await client.post("/v1/messages", content=body,
                                     headers={"content-type": "application/json"})

    plain = _req()
    with_skill = _req(system="Rules from the token-lean skill: be terse.")
    assert _run(go(plain)).status_code == 200
    assert _run(go(with_skill)).status_code == 200

    rows = db_session.scalars(select(LLMRequest).where(
        LLMRequest.provider == "proxy").order_by(LLMRequest.id)).all()
    assert [r.token_lean_active for r in rows] == [False, True]


# ---- doctor: measured skill effect -----------------------------------------

@dataclass
class Row:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int | None = 0
    cache_creation_input_tokens: int | None = 0
    tool_result_chars: int = 0
    token_lean_active: bool = False


def test_skill_effect_measured_when_groups_big_enough():
    rows = ([Row(output_tokens=600, token_lean_active=True)] * 5
            + [Row(output_tokens=1_000)] * 5)
    e = D.skill_effect(rows)
    assert (e.active_requests, e.inactive_requests) == (5, 5)
    assert e.measured_reduction == pytest.approx(0.4)        # 1000 -> 600

    # one side too small -> no claim
    small = rows[:4] + [Row(output_tokens=1_000)] * 6
    assert D.skill_effect(small).measured_reduction is None
    # skill making output LONGER is reported, not hidden
    worse = ([Row(output_tokens=1_500, token_lean_active=True)] * 5
             + [Row(output_tokens=1_000)] * 5)
    assert D.skill_effect(worse).measured_reduction == pytest.approx(-0.5)


def test_r1_uses_measured_reduction_when_available():
    rows = ([Row(input_tokens=10_000, output_tokens=30_000,
                 token_lean_active=True)] * 5
            + [Row(input_tokens=10_000, output_tokens=60_000)] * 5)
    report = D.examine(rows, PIN, POUT, retrieval_queries=50,
                       ordered_total_input=[10_000] * 10)
    r1 = next(d for d in report.diagnoses if d.code == "output_dominant")
    assert "measured on your traffic" in r1.assumption
    # measured reduction = 1 - 30k/60k = 50%, applied to total output spend
    assert report.skill_effect is not None
    assert report.skill_effect.measured_reduction == pytest.approx(0.5)
    assert r1.est_saving_usd == pytest.approx(
        report.breakdown.output_usd * 0.5, abs=1e-4)


def test_r1_falls_back_to_assumption_without_measurement():
    rows = [Row(input_tokens=10_000, output_tokens=50_000)]
    r1 = D.examine(rows, PIN, POUT).diagnoses[0]
    assert r1.code == "output_dominant"
    assert "assumes a 25%" in r1.assumption


@pytest.mark.db
def test_endpoint_exposes_skill_effect(engine, db_session):
    from fastapi.testclient import TestClient

    from coderag.api.app import app, get_session

    for active, out in [(True, 600)] * 5 + [(False, 1_000)] * 5:
        db_session.add(LLMRequest(
            provider="proxy", model="m", input_tokens=1_000, output_tokens=out,
            cached_input_tokens=0, token_lean_active=active,
            latency_ms=1, success=True,
        ))
    db_session.commit()

    app.dependency_overrides[get_session] = lambda: db_session
    try:
        with TestClient(app) as client:
            data = client.get("/doctor").json()
    finally:
        app.dependency_overrides.pop(get_session)
    se = data["skill_effect"]
    assert se["active_requests"] == 5 and se["inactive_requests"] == 5
    assert se["measured_reduction"] == pytest.approx(0.4)


@pytest.mark.db
def test_cli_doctor_shows_measured_effect(engine, db_session):
    from typer.testing import CliRunner

    from coderag.cli.main import app as cli_app

    for active, out in [(True, 600)] * 5 + [(False, 1_000)] * 5:
        db_session.add(LLMRequest(
            provider="proxy", model="m", input_tokens=1_000, output_tokens=out,
            cached_input_tokens=0, token_lean_active=active,
            latency_ms=1, success=True,
        ))
    db_session.commit()

    result = CliRunner().invoke(cli_app, ["doctor"])
    assert result.exit_code == 0
    assert "/token-lean effect (measured)" in result.output
    assert "40% less" in result.output
