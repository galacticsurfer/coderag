"""Hybrid retrieval engine.

Runs the configured retrievers, fuses them with RRF, (optionally, from Phase 4)
expands one graph hop, loads symbol metadata, and returns explainable
``Candidate``s. Every query is scoped to a single ``repository_id`` — there is no
retrieval path that ignores it (ADR-006).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from coderag.core.config import Settings, get_settings
from coderag.db.models import Symbol
from coderag.retrieval.base import (
    EXACT_SYMBOL,
    GRAPH,
    LEXICAL,
    SEMANTIC,
    Candidate,
    RawHit,
    Retriever,
)
from coderag.retrieval.fusion import reciprocal_rank_fusion
from coderag.retrieval.lexical import LexicalRetriever
from coderag.retrieval.symbol import SymbolRetriever


@dataclass
class RetrievalOutcome:
    candidates: list[Candidate]
    latency_ms: float
    per_source_counts: dict[str, int]


class RetrievalEngine:
    def __init__(
        self,
        retrievers: list[Retriever],
        settings: Settings | None = None,
        graph_expander=None,
    ) -> None:
        self.retrievers = retrievers
        self.settings = settings or get_settings()
        self.graph_expander = graph_expander  # set in Phase 4

    def _weights(self) -> dict[str, float]:
        s = self.settings
        return {
            EXACT_SYMBOL: s.weight_symbol,
            LEXICAL: s.weight_lexical,
            SEMANTIC: s.weight_semantic,
            GRAPH: s.weight_graph,
        }

    def _source_limit(self, source: str) -> int:
        s = self.settings
        return {
            EXACT_SYMBOL: s.symbol_limit,
            LEXICAL: s.lexical_limit,
            SEMANTIC: s.semantic_limit,
        }.get(source, s.lexical_limit)

    def search(
        self, session: Session, repository_id: int, query: str, top_n: int | None = None
    ) -> RetrievalOutcome:
        started = time.perf_counter()
        source_hits: dict[str, list[RawHit]] = {}
        per_source_counts: dict[str, int] = {}
        for retriever in self.retrievers:
            hits = retriever.retrieve(
                session, repository_id, query, self._source_limit(retriever.source)
            )
            source_hits[retriever.source] = hits
            per_source_counts[retriever.source] = len(hits)

        fused = reciprocal_rank_fusion(source_hits, self._weights(), self.settings.rrf_k)
        fused = fused[: self.settings.max_candidates]

        candidates = self._load_candidates(session, repository_id, fused)

        # Phase 4: bounded one-hop graph expansion (added candidates with graph reasons).
        if self.graph_expander is not None and self.settings.graph_enabled:
            candidates = self.graph_expander.expand(
                session, repository_id, candidates, query
            )

        candidates.sort(key=lambda c: c.fused_score, reverse=True)
        if top_n is not None:
            candidates = candidates[:top_n]
        latency_ms = (time.perf_counter() - started) * 1000
        return RetrievalOutcome(
            candidates=candidates,
            latency_ms=latency_ms,
            per_source_counts=per_source_counts,
        )

    def _load_candidates(self, session, repository_id, fused) -> list[Candidate]:
        if not fused:
            return []
        by_id = {e.symbol_id: e for e in fused}
        rows = session.execute(
            select(
                Symbol.id, Symbol.qualified_name, Symbol.symbol_name, Symbol.symbol_type,
                Symbol.file_path, Symbol.start_line, Symbol.end_line, Symbol.token_count,
                Symbol.signature, Symbol.docstring,
            )
            # repository scope is enforced here as well as in each retriever.
            .where(Symbol.repository_id == repository_id)
            .where(Symbol.id.in_(list(by_id)))
        ).all()
        candidates: list[Candidate] = []
        for r in rows:
            e = by_id[r.id]
            candidates.append(
                Candidate(
                    symbol_id=r.id,
                    qualified_name=r.qualified_name,
                    symbol_name=r.symbol_name,
                    symbol_type=r.symbol_type,
                    file_path=r.file_path,
                    start_line=r.start_line,
                    end_line=r.end_line,
                    token_count=r.token_count,
                    reasons=set(e.reasons),
                    source_ranks=dict(e.source_ranks),
                    source_scores=dict(e.source_scores),
                    fused_score=e.score,
                    signature=r.signature,
                    docstring=r.docstring,
                )
            )
        return candidates


def build_engine(
    settings: Settings | None = None,
    embedding_provider=None,
    with_graph: bool = False,
) -> RetrievalEngine:
    """Construct an engine with the default retriever set.

    The semantic retriever (Phase 3) is added when an embedding provider is
    supplied; graph expansion (Phase 4) when ``with_graph`` is set.
    """
    settings = settings or get_settings()
    retrievers: list[Retriever] = [SymbolRetriever(), LexicalRetriever()]
    if embedding_provider is not None:
        from coderag.retrieval.semantic import SemanticRetriever

        retrievers.append(SemanticRetriever(embedding_provider, settings))
    graph_expander = None
    if with_graph:
        from coderag.retrieval.graph import GraphExpander

        graph_expander = GraphExpander(settings)
    return RetrievalEngine(retrievers, settings=settings, graph_expander=graph_expander)
