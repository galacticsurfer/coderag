"""--terse: client-agnostic output discipline, injected and measured."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy import select

from coderag import doctor as D
from coderag.db.models import LLMRequest
from coderag.terse import TERSE_INSTRUCTION, apply_terse

PIN, POUT = 5.0, 25.0


def _body(**over) -> bytes:
    base: dict = {"model": "m", "max_tokens": 100,
                  "messages": [{"role": "user", "content": "hi"}]}
    base.update(over)
    return json.dumps(base).encode()


def test_terse_appends_to_string_system():
    out = apply_terse(_body(system="You are helpful."))
    assert out is not None
    got = json.loads(out)["system"]
    assert got.startswith("You are helpful.")
    assert got.endswith(TERSE_INSTRUCTION)


def test_terse_appends_block_to_list_system():
    system = [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}]
    out = apply_terse(_body(system=system))
    assert out is not None
    got = json.loads(out)["system"]
    assert got[0] == system[0]                           # existing block untouched
    assert got[-1] == {"type": "text", "text": TERSE_INSTRUCTION}


def test_terse_sets_system_when_missing():
    out = apply_terse(_body())
    assert out is not None
    assert json.loads(out)["system"] == TERSE_INSTRUCTION


def test_terse_idempotent_and_guarded():
    once = apply_terse(_body(system="s"))
    assert once is not None
    assert apply_terse(once) is None                     # marker detected -> no-op
    listy = apply_terse(_body(system=[{"type": "text", "text": "s"}]))
    assert listy is not None and apply_terse(listy) is None
    assert apply_terse(b"not json") is None
    assert apply_terse(json.dumps({"model": "m"}).encode()) is None  # no messages
    assert apply_terse(_body(system="s")) == once        # deterministic


# ---- proxy wiring ----------------------------------------------------------

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
def test_proxy_terse_off_by_default_on_when_flagged(engine, db_session):
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return httpx.Response(200, json={"model": "m", "usage": {
            "input_tokens": 1, "output_tokens": 1}})

    async def go(app_kw):
        client, app = _proxy_client(handler, **app_kw)
        async with client:
            await client.post("/v1/messages", content=_body(system="s"),
                              headers={"content-type": "application/json"})
        return app

    _run(go({}))
    assert seen[0] == _body(system="s")                 # default: byte-identical

    app = _run(go({"terse": True}))
    assert b"terse-output rules" in seen[1]
    assert app.state.terse_totals["requests_tersed"] == 1

    rows = db_session.scalars(select(LLMRequest).order_by(LLMRequest.id)).all()
    assert [r.terse_applied for r in rows] == [False, True]


# ---- doctor + endpoint -----------------------------------------------------

@dataclass
class Row:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int | None = 0
    cache_creation_input_tokens: int | None = 0
    tool_result_chars: int = 0
    tool_schema_chars: int = 0
    token_lean_active: bool = False
    model: str = "claude-sonnet-5"
    requested_model: str | None = None
    success: bool = True
    compression_chars_saved: int = 0
    cap_applied: bool = False
    terse_applied: bool = False


def test_terse_effect_measured():
    rows = ([Row(output_tokens=700, terse_applied=True)] * 5
            + [Row(output_tokens=1_000)] * 5)
    e = D.terse_effect(rows)
    assert e.measured_reduction == pytest.approx(0.3)
    report = D.examine(rows, PIN, POUT, retrieval_queries=50)
    assert report.terse_effect is not None
    assert report.terse_effect.measured_reduction == pytest.approx(0.3)


@pytest.mark.db
def test_endpoint_and_cli_show_terse_effect(engine, db_session):
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from coderag.api.app import app, get_session
    from coderag.cli.main import app as cli_app

    for terse, out in [(True, 700)] * 5 + [(False, 1_000)] * 5:
        db_session.add(LLMRequest(
            provider="proxy", model="claude-sonnet-5",
            input_tokens=1_000, output_tokens=out, cached_input_tokens=0,
            terse_applied=terse, latency_ms=1, success=True))
    db_session.commit()

    app.dependency_overrides[get_session] = lambda: db_session
    try:
        with TestClient(app) as client:
            data = client.get("/doctor").json()
    finally:
        app.dependency_overrides.pop(get_session)
    assert data["terse_effect"]["measured_reduction"] == pytest.approx(0.3)

    result = CliRunner().invoke(cli_app, ["doctor"])
    assert result.exit_code == 0
    assert "--terse (measured)" in result.output
    assert "30% less" in result.output
