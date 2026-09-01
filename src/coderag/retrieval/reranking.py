"""Optional reranking (Phase 9).

Reranking is OFF by default because it adds compute/latency. Two implementations:
  * ``DeterministicReranker`` — a transparent, dependency-free re-scoring that
    rewards query/identifier overlap and exact-symbol hits.
  * ``CrossEncoderReranker`` — an optional SentenceTransformers CrossEncoder.
The eval harness can measure quality with/without reranking vs its added latency.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from sqlalchemy.orm import Session

from coderag.core.config import Settings, get_settings
from coderag.core.text import expand_query_terms, split_identifier
from coderag.retrieval.base import EXACT_SYMBOL, Candidate


class Reranker(ABC):
    name: str

    @abstractmethod
    def rerank(
        self, session: Session, repository_id: int, query: str,
        candidates: list[Candidate],
    ) -> list[Candidate]: ...


class DeterministicReranker(Reranker):
    name = "deterministic"

    def rerank(self, session, repository_id, query, candidates):
        qterms = {t.lower() for t in expand_query_terms(query)}
        for c in candidates:
            name_terms = {t.lower() for seg in c.qualified_name.split(".")
                          for t in split_identifier(seg)}
            overlap = len(qterms & name_terms)
            bonus = 0.05 * overlap
            if EXACT_SYMBOL in c.reasons:
                bonus += 0.1
            if c.docstring:
                doc_terms = {t.lower() for t in expand_query_terms(c.docstring)}
                bonus += 0.02 * len(qterms & doc_terms)
            c.rerank_score = c.fused_score + bonus
        return sorted(candidates, key=lambda c: (-(c.rerank_score or 0.0), c.symbol_id))


class CrossEncoderReranker(Reranker):
    name = "cross_encoder"

    def __init__(self, model_name: str, max_candidates: int = 50) -> None:
        try:
            from sentence_transformers import CrossEncoder  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "sentence-transformers is required for CrossEncoderReranker "
                "(install the 'embeddings' extra)."
            ) from exc
        self._model = CrossEncoder(model_name)
        self.max_candidates = max_candidates

    def rerank(self, session, repository_id, query, candidates):
        from sqlalchemy import select

        from coderag.db.models import Symbol

        head = candidates[: self.max_candidates]
        ids = [c.symbol_id for c in head]
        code = {
            row[0]: row[1]
            for row in session.execute(
                select(Symbol.id, Symbol.source_code).where(
                    Symbol.repository_id == repository_id, Symbol.id.in_(ids)
                )
            ).all()
        }
        pairs = [
            (query, f"{c.qualified_name}\n{c.signature or ''}\n{code.get(c.symbol_id, '')}")
            for c in head
        ]
        scores = self._model.predict(pairs) if pairs else []
        for c, sc in zip(head, scores, strict=True):
            c.rerank_score = float(sc)
        reranked = sorted(head, key=lambda c: (-(c.rerank_score or 0.0), c.symbol_id))
        # candidates beyond the reranked head keep their fused order, appended after
        return reranked + candidates[self.max_candidates :]


def get_reranker(settings: Settings | None = None) -> Reranker:
    settings = settings or get_settings()
    model = settings.reranker_model
    if model and model.lower() not in ("", "deterministic"):
        try:
            return CrossEncoderReranker(model)
        except Exception:  # pragma: no cover - graceful fallback when extra missing
            return DeterministicReranker()
    return DeterministicReranker()
