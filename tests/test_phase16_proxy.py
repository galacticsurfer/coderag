"""Observability proxy: byte-fidelity passthrough + real usage recording."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from sqlalchemy import select

from coderag.db.models import LLMRequest
from coderag.proxy import create_app

pytestmark = pytest.mark.db

SSE_BODY = (
    'data: {"type":"message_start","message":{"model":"claude-opus-4-8",'
    '"usage":{"input_tokens":1200,"cache_read_input_tokens":800}}}\n\n'
    'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi"}}\n\n'
    'data: {"type":"message_delta","usage":{"output_tokens":42}}\n\n'
    "data: [DONE]\n\n"
)


def _proxy_client(handler) -> httpx.AsyncClient:
    """An app whose upstream is a MockTransport, wrapped in an ASGI test client."""
    app = create_app("https://upstream.example")
    app.state.client = httpx.AsyncClient(
        base_url="https://upstream.example", transport=httpx.MockTransport(handler)
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.local"
    )


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_non_streaming_passthrough_and_recording(engine, db_session):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("x-api-key")
        seen["body"] = request.content
        return httpx.Response(200, json={
            "model": "claude-opus-4-8",
            "content": [{"type": "text", "text": "hello"}],
            "usage": {"input_tokens": 321, "output_tokens": 7,
                      "cache_read_input_tokens": 100},
        })

    async def go():
        async with _proxy_client(handler) as client:
            return await client.post(
                "/v1/messages",
                headers={"x-api-key": "sk-secret", "anthropic-version": "2023-06-01"},
                json={"model": "claude-opus-4-8", "messages": []},
            )

    resp = _run(go())
    assert resp.status_code == 200
    assert resp.json()["content"][0]["text"] == "hello"      # body passed through
    assert seen["auth"] == "sk-secret"                        # auth forwarded upstream
    assert b"claude-opus-4-8" in seen["body"]                 # request body forwarded

    rows = db_session.scalars(
        select(LLMRequest).where(LLMRequest.provider == "proxy")
    ).all()
    assert len(rows) == 1
    r = rows[0]
    assert (r.model, r.input_tokens, r.output_tokens, r.cached_input_tokens) == (
        "claude-opus-4-8", 321, 7, 100)
    assert r.success is True
    assert "sk-secret" not in (r.error or "")                 # credential never stored


def test_streaming_passthrough_byte_identical_and_usage(engine, db_session):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=SSE_BODY.encode(),
                              headers={"content-type": "text/event-stream"})

    async def go():
        async with _proxy_client(handler) as client:
            return await client.post("/v1/messages", json={"stream": True})

    resp = _run(go())
    assert resp.status_code == 200
    assert resp.content == SSE_BODY.encode()                  # byte-for-byte identical

    r = db_session.scalar(select(LLMRequest).where(LLMRequest.provider == "proxy"))
    assert r is not None
    assert (r.model, r.input_tokens, r.output_tokens, r.cached_input_tokens) == (
        "claude-opus-4-8", 1200, 42, 800)


def test_non_messages_endpoints_are_not_recorded(engine, db_session):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    async def go():
        async with _proxy_client(handler) as client:
            return await client.get("/v1/models")

    assert _run(go()).status_code == 200
    assert db_session.scalar(
        select(LLMRequest).where(LLMRequest.provider == "proxy")) is None


def test_upstream_error_passes_through_and_is_marked(engine, db_session):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"type": "rate_limit_error"}},
                              headers={"retry-after": "7"})

    async def go():
        async with _proxy_client(handler) as client:
            return await client.post("/v1/messages", json={})

    resp = _run(go())
    assert resp.status_code == 429
    assert resp.headers.get("retry-after") == "7"             # headers preserved
    r = db_session.scalar(select(LLMRequest).where(LLMRequest.provider == "proxy"))
    assert r is not None and r.success is False


def test_db_failure_never_breaks_traffic(engine, db_session, monkeypatch):
    """The whole point: observability must not be able to take down the agent."""
    import coderag.proxy as proxy_mod

    def boom(*a, **k):
        raise RuntimeError("db is down")

    monkeypatch.setattr(proxy_mod, "session_scope", boom, raising=False)
    # patch the import inside _record by breaking session_scope at its source
    import coderag.db.base as db_base
    monkeypatch.setattr(db_base, "session_scope", boom)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": "m", "usage": {"input_tokens": 1,
                                                                 "output_tokens": 1}})

    async def go():
        async with _proxy_client(handler) as client:
            return await client.post("/v1/messages", json={})

    resp = _run(go())
    assert resp.status_code == 200                            # traffic unaffected
    assert json.loads(resp.content)["model"] == "m"
