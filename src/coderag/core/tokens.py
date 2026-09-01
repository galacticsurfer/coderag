"""Token counting.

Token accounting is a primary product feature, so counting must be:
  * deterministic and offline by default (HeuristicTokenCounter), and
  * pluggable (TiktokenCounter) for closer estimates.

IMPORTANT: these are *estimates* used for budgeting before we call the LLM.
The authoritative token counts always come back from the LLM provider's usage
report and are stored in ``llm_requests``. We never present an estimate as the
real billed number.
"""

from __future__ import annotations

from typing import Protocol

from coderag.core.config import Settings


class TokenCounter(Protocol):
    def count(self, text: str) -> int: ...

    @property
    def name(self) -> str: ...


class HeuristicTokenCounter:
    """Offline estimator.

    Uses a blend of a per-character ratio and a whitespace token count, which
    tracks BPE tokenisation of source code reasonably well without any network
    access or model download.
    """

    name = "heuristic"

    def count(self, text: str) -> int:
        if not text:
            return 0
        char_estimate = len(text) / 4.0
        word_estimate = len(text.split()) * 1.3
        return max(1, round((char_estimate + word_estimate) / 2))


class TiktokenCounter:
    """Closer estimate using tiktoken (optional dependency, may need network)."""

    def __init__(self, encoding: str = "cl100k_base") -> None:
        import tiktoken  # noqa: PLC0415 (lazy: optional dependency)

        self._enc = tiktoken.get_encoding(encoding)
        self.name = f"tiktoken:{encoding}"

    def count(self, text: str) -> int:
        if not text:
            return 0
        return len(self._enc.encode(text, disallowed_special=()))


def get_token_counter(settings: Settings) -> TokenCounter:
    if settings.token_counter == "tiktoken":
        try:
            return TiktokenCounter(settings.tiktoken_encoding)
        except Exception:  # pragma: no cover - graceful offline fallback
            return HeuristicTokenCounter()
    return HeuristicTokenCounter()
