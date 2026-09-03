"""Auto cache placement, smarter compressors, and the two cache detectors."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import httpx
import pytest

from coderag import compression as C
from coderag import doctor as D
from coderag.cache_placement import apply_auto_cache

PIN, POUT = 5.0, 25.0


# ---- apply_auto_cache ------------------------------------------------------

def _body(**over) -> bytes:
    base: dict = {
        "model": "claude-opus-4-8",
        "max_tokens": 1024,
        "system": "You are helpful.",
        "tools": [{"name": "bash", "input_schema": {"type": "object"}},
                  {"name": "read", "input_schema": {"type": "object"}}],
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [{"type": "text", "text": "yo"}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t", "content": "out"}]},
        ],
    }
    base.update(over)
    return json.dumps(base).encode()


def test_auto_cache_injects_standard_breakpoints():
    out = apply_auto_cache(_body())
    assert out is not None
    data = json.loads(out)
    # last tool, last system block, last block of final message — and only those
    assert "cache_control" not in data["tools"][0]
    assert data["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert data["system"] == [{"type": "text", "text": "You are helpful.",
                               "cache_control": {"type": "ephemeral"}}]
    assert data["messages"][-1]["content"][-1]["cache_control"] == {
        "type": "ephemeral"}
    # content untouched apart from metadata / equivalent block conversion
    assert data["messages"][0] == {"role": "user", "content": "hi"}
    assert json.dumps(data).count('"cache_control"') == 3


def test_auto_cache_never_touches_a_client_that_already_caches():
    body = _body(system=[{"type": "text", "text": "s",
                          "cache_control": {"type": "ephemeral"}}])
    assert apply_auto_cache(body) is None
    # even a breakpoint buried in a message is enough to back off
    body = _body(messages=[{"role": "user", "content": [
        {"type": "text", "text": "x", "cache_control": {"type": "ephemeral"}}]}])
    assert apply_auto_cache(body) is None


def test_auto_cache_guarded_deterministic_idempotent():
    assert apply_auto_cache(b"not json") is None
    assert apply_auto_cache(json.dumps({"model": "m"}).encode()) is None
    a, b = apply_auto_cache(_body()), apply_auto_cache(_body())
    assert a == b                              # deterministic
    assert apply_auto_cache(a) is None         # idempotent: output already caches


def test_auto_cache_string_last_message():
    out = apply_auto_cache(_body(messages=[{"role": "user", "content": "just text"}],
                                 tools=None, system=None))
    assert out is not None
    got = json.loads(out)["messages"][-1]["content"]
    assert got == [{"type": "text", "text": "just text",
                    "cache_control": {"type": "ephemeral"}}]


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
def test_proxy_auto_cache_off_by_default_on_when_flagged(engine, db_session):
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return httpx.Response(200, json={"model": "m", "usage": {
            "input_tokens": 1, "output_tokens": 1}})

    async def go(app_kw):
        client, app = _proxy_client(handler, **app_kw)
        async with client:
            await client.post("/v1/messages", content=_body(),
                              headers={"content-type": "application/json"})
        return app

    _run(go({}))
    assert seen[0] == _body()                          # default: byte-identical

    app = _run(go({"auto_cache": True}))
    assert b"cache_control" in seen[1]                 # flagged: breakpoints added
    assert app.state.auto_cache_totals["requests_cached"] == 1


# ---- smarter compressors ---------------------------------------------------

def test_elision_keeps_error_and_warning_lines():
    lines = [f"line {i}: routine output that pads the middle" for i in range(400)]
    lines[200] = "ERROR: connection refused to db:5432"
    lines[201] = "WARNING: retrying in 5s"
    text = "\n".join(lines)
    out = C.elide_middle(text, threshold=2000, keep=800)
    assert "ERROR: connection refused to db:5432" in out
    assert "WARNING: retrying in 5s" in out
    assert "kept 2 error/warning lines" in out
    assert len(out) < len(text)


def test_diffs_are_exempt_from_elision(tmp_path):
    hunk = "@@ -1,3 +1,4 @@\n context\n-removed line\n+added line\n context\n"
    diff = "diff --git a/f.py b/f.py\n" + hunk * 300     # big, but all signal
    out = C.compress_text(diff, threshold=2000, keep=800, directory=tmp_path)
    assert "coderag_expand" not in out                   # nothing elided
    assert out.count("+added line") == 300               # every change survives


def test_base64_runs_are_elided_and_recoverable(tmp_path):
    blob = "iVBORw0KGgo" * 400                            # ~4400 chars, base64-ish
    text = f"screenshot saved:\n{blob}\ndone."
    out = C.elide_base64_runs(text, directory=tmp_path)
    assert blob not in out and "encoded data" in out
    key = out.split('coderag_expand("')[1].split('"')[0]
    assert C.load_original(key, tmp_path) == blob
    # short runs untouched
    assert C.elide_base64_runs("abc123 " * 10, directory=tmp_path) == "abc123 " * 10


def test_upgraded_pipeline_still_deterministic(tmp_path):
    lines = [f"line {i}: unique padding content for the block" for i in range(300)]
    lines[150] = "FATAL: out of memory"
    body = json.dumps({"messages": [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t",
         "content": "\n".join(lines) + "\n" + "QUFBQmJiY2M0" * 300}]}]}).encode()
    r1 = C.compress_messages_body(body, threshold=2000, keep=800, directory=tmp_path)
    r2 = C.compress_messages_body(body, threshold=2000, keep=800, directory=tmp_path)
    assert r1 is not None and r1[0] == r2[0]
    assert b"FATAL: out of memory" in r1[0]


# ---- doctor: R6 cache churn / R7 no caching --------------------------------

@dataclass
class Row:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int | None = 0
    cache_creation_input_tokens: int | None = 0
    tool_result_chars: int = 0
    token_lean_active: bool = False


def codes(rows, **kw) -> set[str]:
    return {d.code for d in D.examine(rows, PIN, POUT, **kw).diagnoses}


def test_r7_no_caching_fires_and_recommends_auto_cache():
    rows = [Row(input_tokens=20_000, output_tokens=100) for _ in range(6)]
    ds = D.examine(rows, PIN, POUT).diagnoses
    got = {d.code for d in ds}
    assert "no_caching" in got
    assert "cache_misses" not in got                    # R7 takes precedence
    r7 = next(d for d in ds if d.code == "no_caching")
    assert "--auto-cache" in r7.action
    # saving: 90% of 120k fresh @ $5/M at (1 - 0.1)
    assert r7.est_saving_usd == pytest.approx(
        120_000 * 0.9 / 1e6 * PIN * 0.9, abs=1e-4)


def test_r7_silent_on_small_or_cached_traffic():
    small = [Row(input_tokens=1_000, output_tokens=50) for _ in range(6)]
    assert "no_caching" not in codes(small)
    cached = [Row(input_tokens=20_000, output_tokens=100,
                  cached_input_tokens=50_000) for _ in range(6)]
    assert "no_caching" not in codes(cached)


def test_r6_cache_churn_fires_on_wasted_writes():
    rows = [Row(input_tokens=2_000, output_tokens=100,
                cached_input_tokens=1_000, cache_creation_input_tokens=40_000)
            for _ in range(6)]
    ds = D.examine(rows, PIN, POUT).diagnoses
    r6 = next(d for d in ds if d.code == "cache_churn")
    # premium: 240k written * 0.25x * $5/M
    assert r6.est_saving_usd == pytest.approx(240_000 * 0.25 / 1e6 * PIN, abs=1e-4)


def test_r6_silent_when_writes_get_read_back():
    rows = [Row(input_tokens=2_000, output_tokens=100,
                cached_input_tokens=100_000, cache_creation_input_tokens=10_000)
            for _ in range(6)]
    assert "cache_churn" not in codes(rows)


def test_r2_still_fires_when_cache_active_but_ineffective():
    rows = [Row(input_tokens=15_000, output_tokens=100,
                cached_input_tokens=1_000, cache_creation_input_tokens=500)
            for _ in range(6)]
    got = codes(rows)
    assert "cache_misses" in got and "no_caching" not in got
