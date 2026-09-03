"""Opt-in terse-output injection: the /token-lean output rules for any client.

The skill only reaches Claude Code sessions that installed it. This transform
makes the same output discipline client-agnostic: the proxy appends one fixed
instruction block to the system prompt of every Messages request. Because the
text is a constant appended in a constant position, the transform is
deterministic and idempotent — resent conversations produce identical bytes,
so prompt caching is preserved.

Like every rewrite in this proxy it is guarded: parse failure or an already-
injected body forwards the original bytes untouched. The proxy records a
``terse_applied`` flag per request so the doctor measures the actual output
effect (terse vs non-terse requests) instead of assuming one.
"""

from __future__ import annotations

import json

# Fixed text — never edit casually: any change invalidates cached prefixes
# that include it, and the marker below is how idempotence is detected.
TERSE_INSTRUCTION = (
    "[coderag terse-output rules] Lead with the answer. No preamble, no "
    "restating the request, no closing summary of what you just did. Prefer "
    "diffs and minimal snippets over full files. Keep prose tight; expand "
    "only when explicitly asked."
)
_MARKER = "[coderag terse-output rules]"


def apply_terse(raw: bytes) -> bytes | None:
    """Append the terse-output instruction to a Messages API body.

    Returns the rewritten body, or ``None`` when the original bytes must be
    forwarded untouched (already injected, parse failure, not a dict).
    """
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
            return None
        system = data.get("system")
        if isinstance(system, str):
            if _MARKER in system:
                return None
            data["system"] = system + "\n\n" + TERSE_INSTRUCTION
        elif isinstance(system, list):
            for block in system:
                if (isinstance(block, dict)
                        and _MARKER in str(block.get("text", ""))):
                    return None
            data["system"] = [*system, {"type": "text", "text": TERSE_INSTRUCTION}]
        elif system is None:
            data["system"] = TERSE_INSTRUCTION
        else:
            return None
        return json.dumps(
            data, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    except Exception:  # noqa: BLE001 - any failure means: forward untouched
        return None
