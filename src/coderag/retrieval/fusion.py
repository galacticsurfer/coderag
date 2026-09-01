"""Reciprocal Rank Fusion (RRF).

RRF fuses ranked lists from incomparable sources without score normalisation:
a hit at rank ``r`` (1-based) from a source contributes ``weight / (k + r)``.
Contributions are summed across sources. ``k`` and the per-source weights come
from configuration (ADR-003) — no magic constants here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from coderag.retrieval.base import RawHit


@dataclass
class FusedEntry:
    symbol_id: int
    score: float = 0.0
    reasons: set[str] = field(default_factory=set)
    source_ranks: dict[str, int] = field(default_factory=dict)
    source_scores: dict[str, float] = field(default_factory=dict)


def reciprocal_rank_fusion(
    source_hits: dict[str, list[RawHit]],
    weights: dict[str, float],
    k: int = 60,
) -> list[FusedEntry]:
    entries: dict[int, FusedEntry] = {}
    for source, hits in source_hits.items():
        weight = weights.get(source, 1.0)
        for rank, hit in enumerate(hits, start=1):
            entry = entries.setdefault(hit.symbol_id, FusedEntry(symbol_id=hit.symbol_id))
            entry.score += weight / (k + rank)
            entry.reasons.add(hit.reason or source)
            # keep the best (lowest) rank per source
            if source not in entry.source_ranks or rank < entry.source_ranks[source]:
                entry.source_ranks[source] = rank
                entry.source_scores[source] = hit.score
    return sorted(entries.values(), key=lambda e: e.score, reverse=True)
