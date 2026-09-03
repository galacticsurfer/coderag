"""Persisted lever effects: compression/caps/auto-cache columns, favicon."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy import select

from coderag import doctor as D
from coderag.db.models import LLMRequest

PIN, POUT = 5.0, 25.0

BIG_LOG = "\n".join(
    [f"line {i}: unique build output that pads this block" for i in range(400)])


def _tool_body(model="claude-opus-4-8") -> bytes:
    return json.dumps({
        "model": model, "max_tokens": 32_000,
        "messages": [
            {"role": "user", "content": "run"},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t", "content": BIG_LOG}]},
        ],
    }).encode()


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
def test_proxy_persists_all_lever_fields(engine, db_session, tmp_path, monkeypatch):
    from coderag import compression as C

    monkeypatch.setattr(C, "RECOVERY_DIR", tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": "m", "usage": {
            "input_tokens": 10, "output_tokens": 5}})

    async def go():
        client, _app = _proxy_client(
            handler, compress=True, cap_output=4_000, auto_cache=True)
        async with client:
            await client.post("/v1/messages", content=_tool_body(),
                              headers={"content-type": "application/json"})

    _run(go())
    r = db_session.scalar(select(LLMRequest).where(LLMRequest.provider == "proxy"))
    assert r is not None
    assert r.compression_chars_saved > 0        # --compress effect persisted
    assert r.cap_applied is True                # max_tokens 32k -> 4k
    assert r.auto_cache_applied is True         # breakpoints were injected
    # off by default: a second app with no flags records zeros/falses
    async def go_plain():
        client, _app = _proxy_client(handler)
        async with client:
            await client.post("/v1/messages", content=_tool_body(),
                              headers={"content-type": "application/json"})
    _run(go_plain())
    rows = db_session.scalars(select(LLMRequest).order_by(LLMRequest.id)).all()
    plain = rows[-1]
    assert (plain.compression_chars_saved, plain.cap_applied,
            plain.auto_cache_applied) == (0, False, False)


# ---- doctor arithmetic -----------------------------------------------------

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
    compression_chars_saved: int = 0
    cap_applied: bool = False


def test_compression_effect_hand_checked():
    rows = [Row(compression_chars_saved=40_000), Row(compression_chars_saved=0),
            Row(compression_chars_saved=8_000)]
    e = D.compression_effect(rows)
    assert e.requests_compressed == 2
    assert e.chars_saved == 48_000
    assert e.est_tokens_saved == 12_000                      # chars / 4
    assert e.est_usd_saved(PIN) == pytest.approx(12_000 / 1e6 * PIN)


def test_cap_effect_measured():
    rows = ([Row(output_tokens=2_000, cap_applied=True)] * 5
            + [Row(output_tokens=4_000)] * 5)
    e = D.cap_effect(rows)
    assert e.measured_reduction == pytest.approx(0.5)
    report = D.examine(rows, PIN, POUT, retrieval_queries=50)
    assert report.cap_effect is not None
    assert report.cap_effect.measured_reduction == pytest.approx(0.5)
    assert report.compression is not None                    # present, zeroed
    assert report.compression.requests_compressed == 0


# ---- endpoint + favicon + dashboard ----------------------------------------

@pytest.mark.db
def test_endpoint_exposes_compression_and_cap_effect(engine, db_session):
    from fastapi.testclient import TestClient

    from coderag.api.app import app, get_session

    for capped, out, saved in ([(True, 500, 20_000)] * 5 + [(False, 1_000, 0)] * 5):
        db_session.add(LLMRequest(
            provider="proxy", model="claude-opus-4-8",
            input_tokens=1_000, output_tokens=out, cached_input_tokens=0,
            cap_applied=capped, compression_chars_saved=saved,
            latency_ms=1, success=True))
    db_session.commit()

    app.dependency_overrides[get_session] = lambda: db_session
    try:
        with TestClient(app) as client:
            data = client.get("/doctor").json()
            icon = client.get("/favicon.ico")
    finally:
        app.dependency_overrides.pop(get_session)

    assert data["compression"]["requests_compressed"] == 5
    assert data["compression"]["est_tokens_saved"] == 25_000
    assert data["compression"]["est_usd_saved"] > 0
    assert data["cap_effect"]["measured_reduction"] == pytest.approx(0.5)

    assert icon.status_code == 200
    assert icon.headers["content-type"].startswith("image/svg+xml")
    assert b"<svg" in icon.content


def test_dashboard_has_favicon_and_logo():
    from coderag.api.dashboard import DASHBOARD_HTML

    assert 'rel="icon"' in DASHBOARD_HTML
    assert "data:image/svg+xml," in DASHBOARD_HTML
    assert "<svg" in DASHBOARD_HTML                     # inline header logo


@pytest.mark.db
def test_cli_doctor_shows_lever_lines(engine, db_session):
    from typer.testing import CliRunner

    from coderag.cli.main import app as cli_app

    for capped, out, saved in ([(True, 500, 20_000)] * 5 + [(False, 1_000, 0)] * 5):
        db_session.add(LLMRequest(
            provider="proxy", model="claude-opus-4-8",
            input_tokens=1_000, output_tokens=out, cached_input_tokens=0,
            cap_applied=capped, compression_chars_saved=saved,
            latency_ms=1, success=True))
    db_session.commit()

    result = CliRunner().invoke(cli_app, ["doctor"])
    assert result.exit_code == 0
    assert "--compress (measured)" in result.output
    assert "output caps (measured)" in result.output
