"""Opt-in automatic prompt-cache placement for the proxy.

Claude Code places its own ``cache_control`` breakpoints, but plenty of
traffic doesn't — raw SDK scripts, other agents, quick integrations. Those
requests resend an identical prefix every turn and bill all of it at the
full input rate when 90%+ of it could bill at the 0.1x cache-read rate.

This module injects the standard incremental-caching pattern into such
requests: a breakpoint on the last tool definition, the last system block,
and the last content block of the final message (<= 3 of the API's 4
allowed breakpoints). The conversation prefix then caches turn over turn.

Rules, in order of importance:

1. **Never touch a request that already uses caching.** Any existing
   ``cache_control`` anywhere means the client manages its own placement —
   forward untouched. This also makes the transform idempotent.
2. **Metadata only.** No prompt content is added, removed, or reordered.
   A string ``system`` / message content is converted to the equivalent
   single text block (the API treats the two forms identically) only when
   a breakpoint must attach to it.
3. **Deterministic and guarded.** Pure function of the body; parse failure
   or nothing-to-do forwards the original bytes (return ``None``).
"""

from __future__ import annotations

import json
from typing import Any

_EPHEMERAL = {"type": "ephemeral"}


def _contains_cache_control(node: Any) -> bool:
    if isinstance(node, dict):
        if "cache_control" in node:
            return True
        return any(_contains_cache_control(v) for v in node.values())
    if isinstance(node, list):
        return any(_contains_cache_control(v) for v in node)
    return False


def _as_text_blocks(value: Any) -> list | None:
    """Coerce a string or block-list into a block list, else None."""
    if isinstance(value, str):
        return [{"type": "text", "text": value}]
    if isinstance(value, list) and value:
        return value
    return None


def _mark_last_block(blocks: list) -> bool:
    """Attach a breakpoint to the last markable block. Returns success."""
    for block in reversed(blocks):
        if isinstance(block, dict) and block.get("type") in (
            "text", "tool_result", "tool_use", "image", "document",
        ):
            block["cache_control"] = dict(_EPHEMERAL)
            return True
    return False


def apply_auto_cache(raw: bytes) -> bytes | None:
    """Inject cache breakpoints into a Messages API body with none.

    Returns the rewritten body, or ``None`` when the original bytes must be
    forwarded untouched (client already caches, parse failure, nothing to do).
    """
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        if _contains_cache_control(data):
            return None
        messages = data.get("messages")
        if not isinstance(messages, list) or not messages:
            return None

        changed = False

        tools = data.get("tools")
        if isinstance(tools, list) and tools and isinstance(tools[-1], dict):
            tools[-1]["cache_control"] = dict(_EPHEMERAL)
            changed = True

        system_blocks = _as_text_blocks(data.get("system"))
        if system_blocks is not None and _mark_last_block(system_blocks):
            data["system"] = system_blocks
            changed = True

        last = messages[-1]
        if isinstance(last, dict):
            content_blocks = _as_text_blocks(last.get("content"))
            if content_blocks is not None and _mark_last_block(content_blocks):
                last["content"] = content_blocks
                changed = True

        if not changed:
            return None
        return json.dumps(
            data, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    except Exception:  # noqa: BLE001 - any failure means: forward untouched
        return None
