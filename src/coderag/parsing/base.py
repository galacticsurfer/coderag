"""Language-independent parsing interfaces and value objects.

A ``LanguageParser`` turns a source file into ``ParsedSymbol`` chunks (and, from
Phase 4, ``ParsedRelationship`` edges). Chunks correspond to code constructs —
module/class/function/method — never fixed-size slices (ADR-002).

Symbols use *local* ids (their index in the returned list) with an optional
``parent_local_id`` so the indexer can wire up ``parent_symbol_id`` after
assigning database ids.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

MODULE = "module"
CLASS = "class"
FUNCTION = "function"
METHOD = "method"

# Relationship types (lightweight code graph).
CONTAINS = "CONTAINS"
CALLS = "CALLS"
IMPORTS = "IMPORTS"
INHERITS = "INHERITS"
REFERENCES = "REFERENCES"
TESTS = "TESTS"


@dataclass
class ParsedSymbol:
    local_id: int
    parent_local_id: int | None
    symbol_name: str
    qualified_name: str
    symbol_type: str
    start_line: int
    end_line: int
    source_code: str
    signature: str | None = None
    docstring: str | None = None
    # Identifiers/words to feed full-text search (name parts, signature, body idents).
    search_terms: list[str] = field(default_factory=list)

    @property
    def source_hash(self) -> str:
        return hashlib.sha256(self.source_code.encode("utf-8")).hexdigest()


@dataclass
class ParsedRelationship:
    """A syntax-derived edge (see Phase 4)."""

    source_local_id: int
    relationship_type: str  # CONTAINS|CALLS|IMPORTS|INHERITS|REFERENCES|TESTS
    target_name: str
    confidence: float = 1.0
    metadata: dict | None = None


@dataclass
class ParseResult:
    symbols: list[ParsedSymbol]
    relationships: list[ParsedRelationship] = field(default_factory=list)


class LanguageParser(ABC):
    language: str = ""
    extensions: tuple[str, ...] = ()

    @abstractmethod
    def parse(self, module_qualified_name: str, source: str) -> ParseResult:
        """Extract symbols (and relationships) from one source file."""
        raise NotImplementedError
