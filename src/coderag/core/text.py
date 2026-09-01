"""Identifier tokenization shared by parsing and lexical retrieval.

Full-text search over code works far better if identifiers are also indexed as
their component words: ``retry_payment`` -> ``retry_payment retry payment`` and
``PaymentService`` -> ``PaymentService payment service``. The same expansion is
applied to queries so ``retry payment`` matches ``retry_payment``.
"""

from __future__ import annotations

import re

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z0-9]+|[A-Z]+|[0-9]+")

# Common language keywords we don't want dominating the search document.
_STOP = frozenset(
    {
        "self", "cls", "def", "class", "return", "import", "from", "as", "if",
        "else", "elif", "for", "while", "try", "except", "finally", "with",
        "pass", "raise", "none", "true", "false", "and", "or", "not", "in", "is",
        "lambda", "yield", "await", "async", "global", "nonlocal", "del", "assert",
    }
)


def split_identifier(identifier: str) -> list[str]:
    """Return the identifier plus its snake/camel component words (lowercased)."""
    out: list[str] = [identifier]
    for part in identifier.split("_"):
        for w in _CAMEL_RE.findall(part):
            if w:
                out.append(w.lower())
    # de-dup preserving order
    seen: set[str] = set()
    result: list[str] = []
    for t in out:
        tl = t.lower()
        if tl and tl not in seen:
            seen.add(tl)
            result.append(t if t == identifier else tl)
    return result


def extract_identifiers(code: str, limit: int = 200) -> list[str]:
    """Unique identifiers appearing in a code snippet (order-preserving, capped)."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _IDENT_RE.finditer(code):
        tok = m.group(0)
        if tok in seen or tok.lower() in _STOP:
            continue
        seen.add(tok)
        out.append(tok)
        if len(out) >= limit:
            break
    return out


def build_search_terms(*groups: list[str], limit: int = 300) -> list[str]:
    """Merge identifier groups into a de-duplicated, word-expanded term list."""
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for ident in group:
            for term in split_identifier(ident):
                key = term.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(term)
                if len(out) >= limit:
                    return out
    return out


def expand_query_terms(query: str) -> list[str]:
    """Tokenize a free-text query into word-expanded search terms."""
    idents = _IDENT_RE.findall(query)
    return build_search_terms(idents)
