"""Opt-in output caps for the proxy: the one *mechanical* output lever.

Output tokens are billed as they are generated — no proxy can shrink them
after the fact. What a proxy *can* do is rewrite the request's generation
parameters before the model starts: clamp ``max_tokens`` and clamp an explicit
extended-thinking ``budget_tokens``. Both genuinely reduce output spend and
both genuinely trade away quality (truncated answers, shallower reasoning),
which is why they are off by default and loudly labelled in the CLI.

Rules, in order of importance:

1. **Clamp only downward.** A request asking for less than the cap is never
   raised to it, and a missing field is never invented.
2. **Adaptive thinking is untouched.** Only an explicit ``budget_tokens`` is
   clamped; ``{"type": "adaptive"}`` has no budget to clamp and rewriting its
   shape would be a behaviour change, not a cap.
3. **Deterministic and guarded.** Pure function; parse failure or no-op means
   the original bytes are forwarded untouched (return ``None``).
"""

from __future__ import annotations

import json


def apply_output_caps(
    raw: bytes,
    max_tokens_cap: int | None = None,
    thinking_budget_cap: int | None = None,
) -> bytes | None:
    """Clamp generation parameters in a Messages API request body.

    Returns the rewritten body, or ``None`` when nothing was (or could be)
    changed and the original bytes must be forwarded untouched.
    """
    if max_tokens_cap is None and thinking_budget_cap is None:
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        changed = False

        mt = data.get("max_tokens")
        if (max_tokens_cap is not None and isinstance(mt, int)
                and mt > max_tokens_cap):
            data["max_tokens"] = max_tokens_cap
            changed = True

        thinking = data.get("thinking")
        if thinking_budget_cap is not None and isinstance(thinking, dict):
            budget = thinking.get("budget_tokens")
            if isinstance(budget, int) and budget > thinking_budget_cap:
                thinking["budget_tokens"] = thinking_budget_cap
                changed = True

        if not changed:
            return None
        return json.dumps(
            data, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    except Exception:  # noqa: BLE001 - any failure means: forward untouched
        return None
