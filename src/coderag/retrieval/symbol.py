"""Exact / qualified symbol retrieval.

Queries that look like a symbol (``PaymentService.retry_payment``,
``retry_payment``) should strongly prioritise an exact match. We match, in
decreasing confidence:
  * exact qualified name,
  * exact symbol name,
  * qualified-name suffix (``….retry_payment``).
Symbol-like tokens embedded in a natural-language query are matched too, at a
lower score.
"""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from coderag.db.models import Symbol
from coderag.retrieval.base import EXACT_SYMBOL, RawHit

_SYMBOL_LIKE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")


def is_symbol_like(text: str) -> bool:
    return bool(_SYMBOL_LIKE.match(text.strip()))


def _candidate_tokens(query: str) -> list[str]:
    q = query.strip()
    tokens: list[str] = []
    if is_symbol_like(q):
        tokens.append(q)
    # symbol-ish tokens inside a natural-language query
    for m in _TOKEN.finditer(query):
        tok = m.group(0)
        if tok == q:
            continue
        if "." in tok or "_" in tok or (len(tok) > 3 and tok[0].isupper()):
            tokens.append(tok)
    # de-dup preserving order
    seen: set[str] = set()
    out = []
    for t in tokens:
        if t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


class SymbolRetriever:
    source = EXACT_SYMBOL

    def retrieve(
        self, session: Session, repository_id: int, query: str, limit: int
    ) -> list[RawHit]:
        tokens = _candidate_tokens(query)
        if not tokens:
            return []

        best: dict[int, float] = {}
        for ti, tok in enumerate(tokens):
            low = tok.lower()
            # whole-query tokens (ti==0 when symbol-like) rank strongest
            base = 1.0 if ti == 0 and is_symbol_like(query) else 0.85
            stmt = (
                select(Symbol.id, Symbol.qualified_name, Symbol.symbol_name)
                .where(Symbol.repository_id == repository_id)
                .where(
                    (func.lower(Symbol.qualified_name) == low)
                    | (func.lower(Symbol.symbol_name) == low)
                    | (func.lower(Symbol.qualified_name).like(f"%.{low}"))
                )
                .limit(limit * 3)
            )
            for sid, qual, name in session.execute(stmt):
                ql = qual.lower()
                if ql == low:
                    score = base
                elif name.lower() == low:
                    score = base * 0.95
                else:  # suffix match
                    score = base * 0.9
                if score > best.get(sid, 0.0):
                    best[sid] = score

        ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)[:limit]
        return [RawHit(symbol_id=sid, score=score) for sid, score in ranked]
