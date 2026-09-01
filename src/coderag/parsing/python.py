"""Tree-sitter based Python parser.

Extracts module/class/function/method symbols with precise line ranges,
signatures, docstrings, and full-text search terms. Chunk boundaries always
follow constructs (ADR-002). Relationship extraction is added in Phase 4 via
``extract_relationships`` on the produced tree.
"""

from __future__ import annotations

import tree_sitter_python as tspython
from tree_sitter import Language, Parser

from coderag.core.text import build_search_terms, extract_identifiers, split_identifier
from coderag.parsing.base import (
    CLASS,
    FUNCTION,
    METHOD,
    MODULE,
    LanguageParser,
    ParsedSymbol,
    ParseResult,
)

_DEF_TYPES = {"function_definition", "class_definition", "decorated_definition"}


class PythonParser(LanguageParser):
    language = "python"
    extensions = (".py",)

    def __init__(self) -> None:
        self._lang = Language(tspython.language(), "python")
        self._parser = Parser()
        self._parser.set_language(self._lang)

    def parse(self, module_qualified_name: str, source: str) -> ParseResult:
        src = source.encode("utf-8")
        tree = self._parser.parse(src)
        root = tree.root_node
        symbols: list[ParsedSymbol] = []
        counter = _Counter()

        def text(node) -> str:
            return src[node.start_byte : node.end_byte].decode("utf-8", "replace")

        # -- module symbol: top-level code excluding class/function bodies ----
        header_nodes = [c for c in root.named_children if c.type not in _DEF_TYPES]
        header_src = "\n".join(text(n) for n in header_nodes)
        mod_end = header_nodes[-1].end_point[0] + 1 if header_nodes else 1
        mod_name = module_qualified_name.rsplit(".", 1)[-1]
        module_sym = ParsedSymbol(
            local_id=counter.next(),
            parent_local_id=None,
            symbol_name=mod_name,
            qualified_name=module_qualified_name,
            symbol_type=MODULE,
            start_line=1,
            end_line=mod_end,
            source_code=header_src,
            signature=None,
            docstring=_docstring_of(root, src),
            search_terms=build_search_terms(
                split_identifier(mod_name),
                extract_identifiers(header_src),
            ),
        )
        symbols.append(module_sym)

        # -- recurse into definitions ----------------------------------------
        def emit(node, parent_local_id: int, qual_prefix: str, enclosing_is_class: bool):
            inner = _unwrap(node)
            name_node = inner.child_by_field_name("name")
            if name_node is None:
                return
            name = text(name_node)
            qualified = f"{qual_prefix}.{name}"
            is_class = inner.type == "class_definition"
            symbol_type = CLASS if is_class else (METHOD if enclosing_is_class else FUNCTION)
            body = inner.child_by_field_name("body")
            signature = _signature(node, inner, body, src)
            source_code = text(node)  # node may be the decorated wrapper
            sym = ParsedSymbol(
                local_id=counter.next(),
                parent_local_id=parent_local_id,
                symbol_name=name,
                qualified_name=qualified,
                symbol_type=symbol_type,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                source_code=source_code,
                signature=signature,
                docstring=_docstring_of(inner, src),
                search_terms=build_search_terms(
                    split_identifier(name),
                    [p for seg in qualified.split(".") for p in split_identifier(seg)],
                    extract_identifiers(signature or ""),
                    extract_identifiers(source_code),
                ),
            )
            symbols.append(sym)
            if body is not None:
                for child in body.named_children:
                    if child.type in _DEF_TYPES:
                        emit(child, sym.local_id, qualified, enclosing_is_class=is_class)

        for child in root.named_children:
            if child.type in _DEF_TYPES:
                emit(child, module_sym.local_id, module_qualified_name, enclosing_is_class=False)

        # Relationships are extracted in Phase 4 (scheduled); none yet.
        return ParseResult(symbols=symbols, relationships=[])


class _Counter:
    def __init__(self) -> None:
        self._n = -1

    def next(self) -> int:
        self._n += 1
        return self._n


def _unwrap(node):
    """decorated_definition -> the inner function/class definition."""
    if node.type == "decorated_definition":
        for c in node.named_children:
            if c.type in ("function_definition", "class_definition"):
                return c
    return node


def _signature(outer, inner, body, src: bytes) -> str | None:
    end = body.start_byte if body is not None else inner.end_byte
    text = src[inner.start_byte : end].decode("utf-8", "replace").strip()
    return text.rstrip(":").strip() or None


def _docstring_of(scope_node, src: bytes) -> str | None:
    """Return the cleaned docstring of a module/function/class node, if any."""
    body = scope_node.child_by_field_name("body")
    candidates = body.named_children if body is not None else scope_node.named_children
    for child in candidates:
        if child.type == "expression_statement" and child.named_children:
            first = child.named_children[0]
            if first.type == "string":
                raw = src[first.start_byte : first.end_byte].decode("utf-8", "replace")
                return _clean_docstring(raw)
        # only the *first* statement can be a docstring
        break
    return None


def _clean_docstring(raw: str) -> str:
    s = raw.strip()
    # strip a leading string prefix (r, b, f, u and combinations)
    i = 0
    while i < len(s) and s[i] in "rbfuRBFU":
        i += 1
    s = s[i:]
    for q in ('"""', "'''", '"', "'"):
        if s.startswith(q) and s.endswith(q) and len(s) >= 2 * len(q):
            s = s[len(q) : -len(q)]
            break
    return s.strip()
