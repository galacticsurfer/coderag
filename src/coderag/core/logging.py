"""Structured logging.

Security rule (SECURITY.md): we NEVER log full source code, full prompts,
secrets, or credentials. Log events carry identifiers (symbol id, path,
counts) — not payloads. Helpers here make the safe path the easy path.
"""

from __future__ import annotations

import logging
import sys

import structlog


class _DynamicStderrFactory:
    """Create a PrintLogger bound to the *current* sys.stderr on each call.

    Resolving the stream dynamically (with caching disabled) avoids writing to a
    stale/closed stream — important under pytest, which swaps sys.stderr per test.
    """

    def __call__(self, *args: object) -> structlog.PrintLogger:
        return structlog.PrintLogger(file=sys.stderr)


def configure_logging(level: str = "INFO") -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=_DynamicStderrFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


def preview(text: str, limit: int = 80) -> str:
    """Return a short, safe single-line preview of possibly-sensitive text.

    Use this instead of logging raw code/queries. Truncates and collapses
    whitespace so full payloads never reach the logs.
    """
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[:limit] + "…"
