"""Lexical retrieval via PostgreSQL full-text search.

Searches the generated ``fts`` tsvector (built from identifiers, signature and
docstring, with identifiers expanded into component words at index time). This
is what reliably finds exact identifiers — ``PAYMENT_RETRY_LIMIT``,
``ERR_PAYMENT_102``, ``/api/v2/payment`` — that embeddings miss.
"""

from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.orm import Session

from coderag.core.text import expand_query_terms
from coderag.retrieval.base import LEXICAL, RawHit

_SAFE = re.compile(r"[A-Za-z0-9_]+")


def _build_tsquery(query: str) -> str:
    """OR-combine word-expanded, sanitised query terms into a to_tsquery string."""
    terms = expand_query_terms(query)
    safe: list[str] = []
    seen: set[str] = set()
    for t in terms:
        for tok in _SAFE.findall(t):
            low = tok.lower()
            if low and low not in seen:
                seen.add(low)
                safe.append(low)
    return " | ".join(safe)


class LexicalRetriever:
    source = LEXICAL

    def retrieve(
        self, session: Session, repository_id: int, query: str, limit: int
    ) -> list[RawHit]:
        tsq = _build_tsquery(query)
        if not tsq:
            return []
        rows = session.execute(
            text(
                """
                SELECT id, ts_rank(fts, q) AS rank
                FROM symbols, to_tsquery('english', :tsq) AS q
                WHERE repository_id = :rid AND fts @@ q
                ORDER BY rank DESC, id ASC
                LIMIT :limit
                """
            ),
            {"tsq": tsq, "rid": repository_id, "limit": limit},
        ).all()
        return [RawHit(symbol_id=r[0], score=float(r[1])) for r in rows]
