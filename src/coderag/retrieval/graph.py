"""Structural retrieval: bounded one-hop graph expansion.

From the highest-confidence candidates, pull in directly related symbols —
parents, children, callers, callees, imports, base classes, and tests — each
tagged with a specific reason (``graph_caller``, ``graph_test``, …). Expansion is
strictly bounded (depth 1, max added candidates, max added tokens) so context
can never explode (ADR-007 / spec §10D).
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from coderag.core.config import Settings, get_settings
from coderag.db.models import Symbol, SymbolRelationship
from coderag.parsing.base import CALLS, CONTAINS, IMPORTS, INHERITS, TESTS
from coderag.retrieval.base import Candidate

# (relationship_type, seed_is_source) -> reason
_REASON = {
    (CONTAINS, True): "graph_child",
    (CONTAINS, False): "graph_parent",
    (CALLS, True): "graph_callee",
    (CALLS, False): "graph_caller",
    (IMPORTS, True): "graph_import",
    (IMPORTS, False): "graph_importer",
    (INHERITS, True): "graph_base",
    (INHERITS, False): "graph_subclass",
    (TESTS, True): "graph_tested",
    (TESTS, False): "graph_test",
}


class GraphExpander:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def expand(
        self, session: Session, repository_id: int, candidates: list[Candidate],
        query: str | None = None,
    ) -> list[Candidate]:
        if not candidates:
            return candidates
        s = self.settings
        by_id = {c.symbol_id: c for c in candidates}
        seeds = candidates[: max(1, s.graph_max_candidates)]
        seed_ids = [c.symbol_id for c in seeds]
        seed_score = {c.symbol_id: c.fused_score for c in seeds}

        edges = session.execute(
            select(
                SymbolRelationship.source_symbol_id,
                SymbolRelationship.target_symbol_id,
                SymbolRelationship.relationship_type,
                SymbolRelationship.confidence,
            ).where(
                SymbolRelationship.repository_id == repository_id,
                SymbolRelationship.target_symbol_id.isnot(None),
                or_(
                    SymbolRelationship.source_symbol_id.in_(seed_ids),
                    SymbolRelationship.target_symbol_id.in_(seed_ids),
                ),
            )
        ).all()

        # neighbour_id -> [reasons, accumulated score]
        additions: dict[int, tuple[set[str], float]] = {}

        def note(neighbor: int, reason: str, contribution: float) -> None:
            reasons, score = additions.get(neighbor, (set(), 0.0))
            reasons.add(reason)
            additions[neighbor] = (reasons, score + contribution)

        seed_set = set(seed_ids)
        for src, tgt, rtype, conf in edges:
            for seed, other, seed_is_source in (
                (src, tgt, True), (tgt, src, False),
            ):
                if seed in seed_set and other is not None and other != seed:
                    reason = _REASON.get((rtype, seed_is_source))
                    if reason is None:
                        continue
                    contribution = s.weight_graph * seed_score.get(seed, 0.0) * float(conf)
                    note(other, reason, contribution)

        # 1) enrich existing candidates in place
        new_ids: list[int] = []
        for nid, (reasons, score) in additions.items():
            if nid in by_id:
                by_id[nid].reasons |= reasons
                by_id[nid].fused_score += score
            else:
                new_ids.append(nid)

        # 2) add new neighbours, bounded by count and token budget
        if new_ids:
            new_ids.sort(key=lambda i: additions[i][1], reverse=True)
            metadata = self._load_symbols(session, repository_id, new_ids)
            token_budget = s.graph_max_tokens
            added = 0
            for nid in new_ids:
                if added >= s.graph_max_candidates:
                    break
                meta = metadata.get(nid)
                if meta is None:
                    continue
                if meta.token_count > token_budget:
                    continue
                reasons, score = additions[nid]
                token_budget -= meta.token_count
                added += 1
                meta.reasons = set(reasons)
                meta.fused_score = score
                candidates.append(meta)

        return candidates

    def _load_symbols(
        self, session: Session, repository_id: int, ids: list[int]
    ) -> dict[int, Candidate]:
        rows = session.execute(
            select(
                Symbol.id, Symbol.qualified_name, Symbol.symbol_name, Symbol.symbol_type,
                Symbol.file_path, Symbol.start_line, Symbol.end_line, Symbol.token_count,
                Symbol.signature, Symbol.docstring,
            ).where(Symbol.repository_id == repository_id, Symbol.id.in_(ids))
        ).all()
        return {
            r.id: Candidate(
                symbol_id=r.id, qualified_name=r.qualified_name, symbol_name=r.symbol_name,
                symbol_type=r.symbol_type, file_path=r.file_path, start_line=r.start_line,
                end_line=r.end_line, token_count=r.token_count, signature=r.signature,
                docstring=r.docstring,
            )
            for r in rows
        }
