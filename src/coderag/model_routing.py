"""Opt-in model routing for the proxy: rewrite the requested model.

The bluntest cost lever there is — a cheaper model is cheaper on every
token — and the most dangerous, because it changes answer quality more than
any compressor. So the policy is deliberately dumb: the **user** supplies
explicit ``source=destination`` pairs and the proxy rewrites exact matches,
nothing else. No heuristics guessing task difficulty, no automatic
downgrades, off by default, loudly labelled in the CLI.

The proxy records the originally requested model alongside the served one,
so ``coderag doctor`` reports what routing actually saved — token counts
times the price difference — instead of an estimate.

Rules:

1. **Exact match only.** ``claude-opus-4-8`` routes ``claude-opus-4-8`` and
   nothing else — no prefix or fuzzy matching that could catch a model the
   user didn't name.
2. **Deterministic and guarded.** Pure function; parse failure or no match
   forwards the original bytes (return ``None``).
"""

from __future__ import annotations

import json


def parse_routes(pairs: list[str]) -> dict[str, str]:
    """Parse ``source=destination`` strings; raises ValueError on bad input."""
    routes: dict[str, str] = {}
    for pair in pairs:
        src, sep, dst = pair.partition("=")
        if not sep or not src.strip() or not dst.strip():
            raise ValueError(
                f"invalid route {pair!r}: expected <source-model>=<dest-model>")
        routes[src.strip()] = dst.strip()
    return routes


def apply_model_route(
    raw: bytes, routes: dict[str, str]
) -> tuple[bytes, str, str] | None:
    """Rewrite the model field of a Messages API body per the route map.

    Returns ``(new_body, requested_model, routed_model)``, or ``None`` when
    the original bytes must be forwarded untouched.
    """
    if not routes:
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        model = data.get("model")
        if not isinstance(model, str) or model not in routes:
            return None
        target = routes[model]
        if target == model:
            return None
        data["model"] = target
        new_raw = json.dumps(
            data, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        return new_raw, model, target
    except Exception:  # noqa: BLE001 - any failure means: forward untouched
        return None
