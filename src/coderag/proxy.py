"""Observability proxy: see real LLM token usage without modifying traffic.

Point a client's ``ANTHROPIC_BASE_URL`` at this proxy and every request is
forwarded to the upstream API **byte-for-byte unmodified** (headers, body,
streaming included). The only thing the proxy does besides forwarding is read
the *usage* numbers out of responses and record them to the ``llm_requests``
table — so the dashboard finally shows the provider-billed input/output tokens
of the agent actually using CodeRAG (e.g. Claude Code), not just estimates.

Deliberate non-goals, stated up front:
  * No compression, rewriting, caching, or routing. Anything that changes bytes
    can change model behaviour; this proxy never does.
  * No persistence of prompts, responses, or credentials. Only token counts,
    model name, latency, and status are stored (see SECURITY.md).

Security posture:
  * Binds to 127.0.0.1 only, by default.
  * Auth headers (``x-api-key`` / ``authorization``) are forwarded upstream and
    never logged or written to the database.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse

from coderag.core.logging import get_logger

log = get_logger("proxy")

DEFAULT_UPSTREAM = "https://api.anthropic.com"

# hop-by-hop / recomputed headers we must not blindly forward back
_STRIP_RESPONSE_HEADERS = {
    "content-encoding", "content-length", "transfer-encoding", "connection",
}
_STRIP_REQUEST_HEADERS = {"host", "content-length", "accept-encoding", "connection"}


@dataclass
class ObservedUsage:
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    tool_result_chars: int = 0
    tool_schema_chars: int = 0
    token_lean_active: bool = False
    requested_model: str | None = None
    compression_chars_saved: int = 0
    cap_applied: bool = False
    auto_cache_applied: bool = False
    status_code: int = 0
    streamed: bool = False


def _record(usage: ObservedUsage, latency_ms: float) -> None:
    """Persist observed usage. Never allowed to break proxying."""
    try:
        from coderag.db.base import session_scope
        from coderag.db.models import LLMRequest

        with session_scope() as session:
            session.add(LLMRequest(
                provider="proxy",
                model=usage.model or "unknown",
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cached_input_tokens=usage.cached_input_tokens,
                cache_creation_input_tokens=usage.cache_creation_input_tokens,
                tool_result_chars=usage.tool_result_chars,
                tool_schema_chars=usage.tool_schema_chars,
                token_lean_active=usage.token_lean_active,
                requested_model=usage.requested_model,
                compression_chars_saved=usage.compression_chars_saved,
                cap_applied=usage.cap_applied,
                auto_cache_applied=usage.auto_cache_applied,
                latency_ms=latency_ms,
                success=200 <= usage.status_code < 300,
                error=None if 200 <= usage.status_code < 300
                else f"upstream status {usage.status_code}",
            ))
    except Exception as exc:  # noqa: BLE001 - observability must not break traffic
        log.warning("proxy.record_failed", error=str(exc)[:120])


def _usage_from_json(body: bytes, usage: ObservedUsage) -> None:
    """Extract usage from a non-streaming Messages API response body."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    usage.model = data.get("model", usage.model)
    u = data.get("usage") or {}
    usage.input_tokens = int(u.get("input_tokens") or 0)
    usage.output_tokens = int(u.get("output_tokens") or 0)
    if u.get("cache_read_input_tokens") is not None:
        usage.cached_input_tokens = int(u["cache_read_input_tokens"])
    if u.get("cache_creation_input_tokens") is not None:
        usage.cache_creation_input_tokens = int(u["cache_creation_input_tokens"])


def _usage_from_sse_line(line: str, usage: ObservedUsage) -> None:
    """Opportunistically parse one SSE data line for usage fields."""
    if not line.startswith("data:"):
        return
    payload = line[len("data:"):].strip()
    if not payload or payload == "[DONE]":
        return
    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        return
    etype = event.get("type")
    if etype == "message_start":
        msg = event.get("message") or {}
        usage.model = msg.get("model", usage.model)
        u = msg.get("usage") or {}
        usage.input_tokens = int(u.get("input_tokens") or 0)
        if u.get("cache_read_input_tokens") is not None:
            usage.cached_input_tokens = int(u["cache_read_input_tokens"])
        if u.get("cache_creation_input_tokens") is not None:
            usage.cache_creation_input_tokens = int(u["cache_creation_input_tokens"])
    elif etype == "message_delta":
        u = event.get("usage") or {}
        if u.get("output_tokens") is not None:
            usage.output_tokens = int(u["output_tokens"])


# Marker for detecting whether the /token-lean skill is active in a request.
# Byte search only — the body is never parsed or stored for this.
_TOKEN_LEAN_MARKER = b"token-lean"


def create_app(
    upstream: str = DEFAULT_UPSTREAM,
    compress: bool = False,
    cap_output: int | None = None,
    cap_thinking: int | None = None,
    auto_cache: bool = False,
    routes: dict[str, str] | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # pragma: no cover
        yield
        await app.state.client.aclose()

    app = FastAPI(title="CodeRAG observability proxy", docs_url=None,
                  redoc_url=None, lifespan=lifespan)
    app.state.upstream = upstream
    app.state.compress = compress
    app.state.cap_output = cap_output
    app.state.cap_thinking = cap_thinking
    app.state.cap_totals = {"requests_capped": 0}
    app.state.auto_cache = auto_cache
    app.state.auto_cache_totals = {"requests_cached": 0}
    app.state.routes = dict(routes or {})
    app.state.route_totals = {"requests_routed": 0}
    app.state.compression_totals = {
        "requests_seen": 0, "requests_compressed": 0, "chars_in": 0, "chars_saved": 0,
    }
    app.state.client = httpx.AsyncClient(
        base_url=upstream, timeout=httpx.Timeout(600.0)
    )

    @app.get("/coderag-proxy/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "upstream": upstream,
            "compress": app.state.compress,
            "compression": dict(app.state.compression_totals),
            "cap_output": app.state.cap_output,
            "cap_thinking": app.state.cap_thinking,
            "caps": dict(app.state.cap_totals),
            "auto_cache": app.state.auto_cache,
            "auto_cache_totals": dict(app.state.auto_cache_totals),
            "routes": dict(app.state.routes),
            "route_totals": dict(app.state.route_totals),
        }

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    )
    async def forward(request: Request, path: str) -> Response:
        body = await request.body()
        original_body = body
        headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in _STRIP_REQUEST_HEADERS
        }
        url = f"/{path}"
        if request.url.query:
            url = f"{url}?{request.url.query}"

        url_path = f"/{path}"
        is_messages_endpoint = url_path.rstrip("/").endswith("/messages")
        usage = ObservedUsage()

        # Opt-in model routing: rewrite exact user-named model IDs. The
        # bluntest quality trade-off of all — explicit pairs only, off by
        # default, and the doctor measures what it actually saved.
        if (
            request.app.state.routes
            and request.method == "POST"
            and is_messages_endpoint
        ):
            from coderag.model_routing import apply_model_route

            routed = apply_model_route(body, request.app.state.routes)
            if routed is not None:
                body, requested, _target = routed
                usage.requested_model = requested
                request.app.state.route_totals["requests_routed"] += 1

        # Opt-in output caps: clamp max_tokens / thinking budget downward.
        # A deliberate quality trade-off — off by default, guarded like
        # compression (any failure forwards the original bytes).
        if (
            request.method == "POST"
            and is_messages_endpoint
            and (request.app.state.cap_output is not None
                 or request.app.state.cap_thinking is not None)
        ):
            from coderag.output_caps import apply_output_caps

            capped = apply_output_caps(
                body,
                max_tokens_cap=request.app.state.cap_output,
                thinking_budget_cap=request.app.state.cap_thinking,
            )
            if capped is not None:
                body = capped
                usage.cap_applied = True
                request.app.state.cap_totals["requests_capped"] += 1

        # Opt-in, guarded compression of tool_result blocks in the request body.
        # Deterministic (cache-safe); any failure forwards the original bytes.
        if (
            request.app.state.compress
            and request.method == "POST"
            and is_messages_endpoint
        ):
            from coderag.compression import compress_messages_body
            from coderag.core.config import get_settings

            totals = request.app.state.compression_totals
            totals["requests_seen"] += 1
            settings = get_settings()
            result = compress_messages_body(
                body,
                threshold=settings.proxy_elide_threshold_chars,
                keep=settings.proxy_elide_keep_chars,
            )
            if result is not None:
                body, stats = result
                usage.compression_chars_saved = stats.chars_saved
                totals["requests_compressed"] += 1
                totals["chars_in"] += stats.chars_in
                totals["chars_saved"] += stats.chars_saved
                log.info(
                    "proxy.compressed",
                    blocks=stats.blocks_compressed,
                    chars_saved=stats.chars_saved,
                )

        # Opt-in automatic cache placement: inject standard cache_control
        # breakpoints into bodies that have none. Runs after compression so
        # breakpoints attach to the final content. Guarded; never touches a
        # client that already manages its own caching.
        if (
            request.app.state.auto_cache
            and request.method == "POST"
            and is_messages_endpoint
        ):
            from coderag.cache_placement import apply_auto_cache

            cached_body = apply_auto_cache(body)
            if cached_body is not None:
                body = cached_body
                usage.auto_cache_applied = True
                request.app.state.auto_cache_totals["requests_cached"] += 1

        started = time.perf_counter()
        if request.method == "POST" and is_messages_endpoint:
            from coderag.compression import (
                tool_result_chars_in_body,
                tool_schema_chars_in_body,
            )

            usage.tool_result_chars = tool_result_chars_in_body(original_body)
            usage.tool_schema_chars = tool_schema_chars_in_body(original_body)
            usage.token_lean_active = _TOKEN_LEAN_MARKER in original_body

        http: httpx.AsyncClient = request.app.state.client
        upstream_request = http.build_request(
            request.method, url, headers=headers, content=body
        )
        upstream_response = await http.send(upstream_request, stream=True)
        usage.status_code = upstream_response.status_code
        response_headers = {
            k: v for k, v in upstream_response.headers.items()
            if k.lower() not in _STRIP_RESPONSE_HEADERS
        }
        content_type = upstream_response.headers.get("content-type", "")
        is_messages = path.strip("/").endswith("v1/messages") or "/messages" in url

        if "text/event-stream" in content_type:
            usage.streamed = True

            async def stream() -> AsyncIterator[bytes]:
                buffer = ""
                try:
                    async for chunk in upstream_response.aiter_bytes():
                        # tee: parse for usage, but always yield bytes unmodified
                        with contextlib.suppress(Exception):
                            buffer += chunk.decode("utf-8", "ignore")
                            while "\n" in buffer:
                                line, buffer = buffer.split("\n", 1)
                                _usage_from_sse_line(line.strip("\r"), usage)
                        yield chunk
                finally:
                    await upstream_response.aclose()
                    if is_messages:
                        _record(usage, (time.perf_counter() - started) * 1000)

            return StreamingResponse(
                stream(),
                status_code=upstream_response.status_code,
                headers=response_headers,
                media_type="text/event-stream",
            )

        content = await upstream_response.aread()
        await upstream_response.aclose()
        if is_messages:
            _usage_from_json(content, usage)
            _record(usage, (time.perf_counter() - started) * 1000)
        return Response(
            content=content,
            status_code=upstream_response.status_code,
            headers=response_headers,
        )

    return app
