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
    CALLS,
    CLASS,
    FUNCTION,
    IMPORTS,
    INHERITS,
    METHOD,
    MODULE,
    LanguageParser,
    ParsedRelationship,
    ParsedSymbol,
    ParseResult,
)

_DEF_TYPES = {"function_definition", "class_definition", "decorated_definition"}


class PythonParser(LanguageParser):
    language = "python"
    extensions = (".py",)

    def __init__(self) -> None:
        # The tree-sitter Python API changed at 0.22: `Language(ptr)` lost its
        # `name` argument and `Parser.set_language()` was replaced by passing the
        # language to the constructor. Support both so the package installs on
        # any platform/version combination (0.21 has no arm64 macOS wheel).
        try:
            self._lang = Language(tspython.language())  # tree-sitter >= 0.22
        except TypeError:  # pragma: no cover - legacy tree-sitter 0.21
            self._lang = Language(tspython.language(), "python")  # type: ignore[call-overload]
        try:
            self._parser = Parser(self._lang)  # tree-sitter >= 0.22
        except TypeError:  # pragma: no cover - legacy tree-sitter 0.21
            self._parser = Parser()
            self._parser.set_language(self._lang)  # type: ignore[attr-defined]

    def parse(self, module_qualified_name: str, source: str) -> ParseResult:
        src = source.encode("utf-8")
        tree = self._parser.parse(src)
        root = tree.root_node
        symbols: list[ParsedSymbol] = []
        relationships: list[ParsedRelationship] = []
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

        # -- module imports --------------------------------------------------
        for node in header_nodes:
            for target in _imports(node, src):
                relationships.append(
                    ParsedRelationship(module_sym.local_id, IMPORTS, target, confidence=0.9)
                )

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

            if is_class:
                for base in _bases(inner, src):
                    relationships.append(
                        ParsedRelationship(sym.local_id, INHERITS, base, confidence=0.9)
                    )
            elif body is not None:
                for target, via in _calls(body, src):
                    relationships.append(
                        ParsedRelationship(
                            sym.local_id, CALLS, target,
                            confidence=0.9 if via == "self" else 0.6,
                            metadata={"via": via},
                        )
                    )

            if body is not None:
                for child in body.named_children:
                    if child.type in _DEF_TYPES:
                        emit(child, sym.local_id, qualified, enclosing_is_class=is_class)

        for child in root.named_children:
            if child.type in _DEF_TYPES:
                emit(child, module_sym.local_id, module_qualified_name, enclosing_is_class=False)

        return ParseResult(symbols=symbols, relationships=relationships)


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


def _node_text(node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _imports(node, src: bytes) -> list[str]:
    """Return imported dotted names from an import/import-from statement."""
    out: list[str] = []
    if node.type == "import_statement":
        for child in node.named_children:
            if child.type in ("dotted_name", "aliased_import"):
                dn = child if child.type == "dotted_name" else child.child_by_field_name("name")
                if dn is not None:
                    out.append(_node_text(dn, src))
    elif node.type == "import_from_statement":
        mod_node = node.child_by_field_name("module_name")
        # module_name field may not be set on older grammars; fall back to first dotted_name
        module = None
        if mod_node is not None:
            module = _node_text(mod_node, src)
        else:
            for child in node.named_children:
                if child.type in ("dotted_name", "relative_import"):
                    module = _node_text(child, src)
                    break
        # imported names are the dotted_name/aliased_import nodes after the module
        names: list[str] = []
        seen_module = False
        for child in node.named_children:
            if not seen_module:
                if module and _node_text(child, src) == module:
                    seen_module = True
                continue
            if child.type == "dotted_name":
                names.append(_node_text(child, src))
            elif child.type == "aliased_import":
                nm = child.child_by_field_name("name")
                if nm is not None:
                    names.append(_node_text(nm, src))
            elif child.type == "wildcard_import":
                pass
        if names and module:
            out.extend(f"{module}.{n}" for n in names)
        elif module:
            out.append(module)
    return out


def _bases(class_inner, src: bytes) -> list[str]:
    sup = class_inner.child_by_field_name("superclasses")
    if sup is None:
        return []
    bases: list[str] = []
    for child in sup.named_children:
        if child.type == "identifier":
            bases.append(_node_text(child, src))
        elif child.type == "attribute":
            attr = child.child_by_field_name("attribute")
            if attr is not None:
                bases.append(_node_text(attr, src))
    return bases


def _calls(body, src: bytes) -> list[tuple[str, str]]:
    """Collect (target_name, via) call targets within a body.

    Does not descend into nested definitions (their calls belong to them).
    ``via`` is "self" for self.method(), else "attr" or "bare".
    """
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def visit(node):
        for child in node.children:
            if child.type in _DEF_TYPES:
                continue  # nested symbol; handled separately
            if child.type == "call":
                fn = child.child_by_field_name("function")
                hit = _call_target(fn, src)
                if hit and hit not in seen:
                    seen.add(hit)
                    out.append(hit)
            visit(child)

    visit(body)
    return out


def _call_target(fn, src: bytes) -> tuple[str, str] | None:
    if fn is None:
        return None
    if fn.type == "identifier":
        return (_node_text(fn, src), "bare")
    if fn.type == "attribute":
        obj = fn.child_by_field_name("object")
        attr = fn.child_by_field_name("attribute")
        if attr is None:
            return None
        via = "self" if obj is not None and _node_text(obj, src) == "self" else "attr"
        return (_node_text(attr, src), via)
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
