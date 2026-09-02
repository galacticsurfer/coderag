"""Cache-safe request compression: determinism, scope, recovery, guards."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from coderag import compression as C

BIG_LOG = "\n".join(
    ["\x1b[32mINFO\x1b[0m connecting to db"] * 5
    + [f"line {i}: some unique build output that pads this block" for i in range(400)]
    + ["\x1b[31mERROR\x1b[0m timeout waiting for lock"] * 4
)


def _body(tool_text: str) -> bytes:
    return json.dumps({
        "model": "claude-opus-4-8",
        "system": "You are 	terse.  Keep   whitespace \x1b[1mquirks\x1b[0m intact.",
        "messages": [
            {"role": "user", "content": "run the tests"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Running."},
                {"type": "tool_use", "id": "t1", "name": "bash", "input": {"command": "pytest"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": tool_text},
            ]},
        ],
    }).encode()


def test_deterministic_across_repeated_calls(tmp_path):
    """Same input must always give identical bytes — the prompt-cache property."""
    raw = _body(BIG_LOG)
    r1 = C.compress_messages_body(raw, threshold=2000, keep=800, directory=tmp_path)
    r2 = C.compress_messages_body(raw, threshold=2000, keep=800, directory=tmp_path)
    assert r1 is not None and r2 is not None
    assert r1[0] == r2[0]


def test_only_tool_results_are_touched(tmp_path):
    raw = _body(BIG_LOG)
    result = C.compress_messages_body(raw, threshold=2000, keep=800, directory=tmp_path)
    assert result is not None
    new = json.loads(result[0])
    old = json.loads(raw)
    # system prompt byte-identical, including ANSI and odd whitespace
    assert new["system"] == old["system"]
    # user text and assistant turn (incl. tool_use input) untouched
    assert new["messages"][0] == old["messages"][0]
    assert new["messages"][1] == old["messages"][1]
    # the tool_result itself did change and shrank
    got = new["messages"][2]["content"][0]["content"]
    assert len(got) < len(BIG_LOG)
    assert "\x1b[" not in got                       # ANSI stripped
    assert "repeated 4 more times" in got           # dedupe marker
    assert 'coderag_expand("' in got                # elision marker with key


def test_elided_original_is_recoverable(tmp_path):
    result = C.compress_messages_body(
        _body(BIG_LOG), threshold=2000, keep=800, directory=tmp_path)
    assert result is not None
    got = json.loads(result[0])["messages"][2]["content"][0]["content"]
    key = got.split('coderag_expand("')[1].split('"')[0]
    original = C.load_original(key, tmp_path)
    # stored original is the pre-transform text (dedupe/ANSI included)
    assert original is not None and original == BIG_LOG


def test_recovery_key_is_validated_against_traversal(tmp_path):
    (tmp_path / "secret.txt").write_text("nope")
    assert C.load_original("../secret", tmp_path) is None
    assert C.load_original("", tmp_path) is None
    assert C.load_original("ZZZZZZZZ", tmp_path) is None


def test_small_or_unhelpful_content_forwards_untouched(tmp_path):
    assert C.compress_messages_body(
        _body("short output, all fine"), threshold=2000, keep=800,
        directory=tmp_path) is None
    assert C.compress_messages_body(b"not json at all", 2000, 800, tmp_path) is None
    assert C.compress_messages_body(
        json.dumps({"model": "m"}).encode(), 2000, 800, tmp_path) is None


def test_transforms_are_conservative():
    assert C.strip_ansi("\x1b[31mred\x1b[0m text") == "red text"
    # runs below the threshold are left alone
    assert C.dedupe_consecutive_lines("a\na\nb") == "a\na\nb"
    deduped = C.dedupe_consecutive_lines("x\nx\nx\nx\ny")
    assert deduped.splitlines()[0] == "x" and "repeated 3 more times" in deduped
    assert C.squeeze_blank_lines("a\n\n\n\n\nb") == "a\n\nb"
    # elide never splits mid-line
    text = "\n".join(f"row-{i}" for i in range(500))
    out = C.elide_middle(text, threshold=1000, keep=400)
    for line in out.splitlines():
        assert line.startswith(("row-", "[coderag:")) or line == ""


def test_proxy_applies_compression_only_when_enabled(engine, db_session, tmp_path,
                                                     monkeypatch):
    pytest.importorskip("fastapi")
    from coderag.proxy import create_app

    monkeypatch.setattr(C, "RECOVERY_DIR", tmp_path)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.setdefault("bodies", []).append(request.content)
        return httpx.Response(200, json={"model": "m", "usage": {
            "input_tokens": 1, "output_tokens": 1}})

    def call(app):
        app.state.client = httpx.AsyncClient(
            base_url="https://upstream.example",
            transport=httpx.MockTransport(handler))

        async def go():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://proxy.local",
            ) as client:
                return await client.post("/v1/messages", content=_body(BIG_LOG),
                                         headers={"content-type": "application/json"})
        return asyncio.new_event_loop().run_until_complete(go())

    raw = _body(BIG_LOG)
    assert call(create_app("https://upstream.example")).status_code == 200
    assert seen["bodies"][0] == raw                      # off by default: untouched

    assert call(create_app("https://upstream.example", compress=True)).status_code == 200
    assert len(seen["bodies"][1]) < len(raw)             # on: upstream got smaller body
    assert b"coderag_expand" in seen["bodies"][1]


def test_mcp_expand_tool_roundtrip(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    monkeypatch.setattr(C, "RECOVERY_DIR", tmp_path)
    from coderag.mcp_server import coderag_expand

    key = C.store_original("the full original log", tmp_path)
    out = coderag_expand(key)
    assert out["text"] == "the full original log"
    assert "error" in coderag_expand("deadbeefdeadbeef")  # unknown key -> clear error
    assert "error" in coderag_expand("../../etc/passwd")  # traversal -> rejected
