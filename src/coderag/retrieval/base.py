"""Retrieval value objects and the Retriever protocol.

A ``Retriever`` returns ``RawHit``s (symbol id + a source-local score) ordered
best-first. The engine fuses hits from all retrievers into ``Candidate``s, each
of which records *why* it was retrieved (its reasons) and its per-source ranks —
so retrieval is explainable, per the design principles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.orm import Session

# Source / reason labels.
EXACT_SYMBOL = "exact_symbol"
LEXICAL = "lexical"
SEMANTIC = "semantic"
GRAPH = "graph"  # refined to graph_call/graph_test/... in Phase 4


@dataclass
class RawHit:
    symbol_id: int
    score: float
    # Optional richer reason (e.g. "graph_call"); defaults to the source label.
    reason: str | None = None


@dataclass
class Candidate:
    symbol_id: int
    qualified_name: str
    symbol_name: str
    symbol_type: str
    file_path: str
    start_line: int
    end_line: int
    token_count: int
    reasons: set[str] = field(default_factory=set)
    source_ranks: dict[str, int] = field(default_factory=dict)
    source_scores: dict[str, float] = field(default_factory=dict)
    fused_score: float = 0.0
    # Populated lazily by the context builder when code is actually needed.
    signature: str | None = None
    docstring: str | None = None
    source_code: str | None = None

    def explain(self) -> str:
        return ",".join(sorted(self.reasons))


class Retriever(Protocol):
    source: str

    def retrieve(
        self, session: Session, repository_id: int, query: str, limit: int
    ) -> list[RawHit]: ...
