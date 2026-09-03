"""Generic tree-sitter parser for TypeScript/JavaScript (incl. React TSX/JSX),
Go, Java, and Rust.

One spec-driven parser instead of five bespoke ones: a ``LanguageSpec`` names
the node types that produce symbols, how to find their names, and how imports
and calls look in that grammar. Chunk boundaries always follow constructs
(ADR-002), same as the Python parser. Docstrings are not extracted (comment
conventions vary too much to do honestly in v1).

React note: most components are ``const Foo = () => ...`` — arrow functions
bound by a variable declarator — so declarators whose value is a function are
emitted as FUNCTION symbols, not skipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tree_sitter import Language, Parser

from coderag.core.text import build_search_terms, extract_identifiers, split_identifier
from coderag.parsing.base import (
    CALLS,
    CLASS,
    FUNCTION,
    IMPORTS,
    METHOD,
    MODULE,
    LanguageParser,
    ParsedRelationship,
    ParsedSymbol,
    ParseResult,
)


@dataclass(frozen=True)
class LanguageSpec:
    language: str
    extensions: tuple[str, ...]
    grammar: str                       # "module:attribute" returning the language ptr
    class_types: frozenset[str]        # emitted as CLASS
    function_types: frozenset[str]     # emitted as FUNCTION (METHOD inside a class)
    body_fields: tuple[str, ...] = ("body",)
    wrapper_types: frozenset[str] = frozenset()   # unwrap (export_statement, ...)
    var_decl_types: frozenset[str] = frozenset()  # declarations holding declarators
    fn_value_types: frozenset[str] = frozenset()  # declarator values that are functions
    import_types: frozenset[str] = frozenset()
    call_type: str = "call_expression"
    call_fn_field: str = "function"
    member_types: dict[str, str] = field(default_factory=dict)  # node type -> name field
    # containers that only group children (Go type_declaration, Rust impl/mod)
    group_types: frozenset[str] = frozenset()


def _load(grammar: str):
    mod_name, attr = grammar.split(":")
    module = __import__(mod_name)
    return getattr(module, attr)()


def _js_spec(language: str, extensions: tuple[str, ...], grammar: str) -> LanguageSpec:
    return LanguageSpec(
        language=language, extensions=extensions, grammar=grammar,
        class_types=frozenset({
            "class_declaration", "abstract_class_declaration",
            "interface_declaration", "enum_declaration",
            "type_alias_declaration",
        }),
        function_types=frozenset({
            "function_declaration", "generator_function_declaration",
            "method_definition",
        }),
        body_fields=("body",),
        wrapper_types=frozenset({"export_statement"}),
        var_decl_types=frozenset({"lexical_declaration", "variable_declaration"}),
        fn_value_types=frozenset({"arrow_function", "function_expression",
                                  "function"}),
        import_types=frozenset({"import_statement"}),
        member_types={"member_expression": "property"},
    )


SPECS: tuple[LanguageSpec, ...] = (
    _js_spec("javascript", (".js", ".jsx", ".mjs", ".cjs"),
             "tree_sitter_javascript:language"),
    _js_spec("typescript", (".ts", ".mts", ".cts"),
             "tree_sitter_typescript:language_typescript"),
    _js_spec("tsx", (".tsx",), "tree_sitter_typescript:language_tsx"),
    LanguageSpec(
        language="go", extensions=(".go",),
        grammar="tree_sitter_go:language",
        class_types=frozenset({"type_spec"}),
        function_types=frozenset({"function_declaration", "method_declaration"}),
        body_fields=("body",),
        group_types=frozenset({"type_declaration"}),
        import_types=frozenset({"import_declaration"}),
        member_types={"selector_expression": "field"},
    ),
    LanguageSpec(
        language="java", extensions=(".java",),
        grammar="tree_sitter_java:language",
        class_types=frozenset({
            "class_declaration", "interface_declaration", "enum_declaration",
            "record_declaration", "annotation_type_declaration",
        }),
        function_types=frozenset({"method_declaration", "constructor_declaration"}),
        body_fields=("body",),
        import_types=frozenset({"import_declaration"}),
        call_type="method_invocation",
        call_fn_field="name",
        member_types={},
    ),
    LanguageSpec(
        language="rust", extensions=(".rs",),
        grammar="tree_sitter_rust:language",
        class_types=frozenset({"struct_item", "enum_item", "trait_item",
                               "union_item"}),
        function_types=frozenset({"function_item", "function_signature_item"}),
        body_fields=("body", "declaration_list"),
        group_types=frozenset({"impl_item", "mod_item"}),
        import_types=frozenset({"use_declaration"}),
        member_types={"field_expression": "field",
                      "scoped_identifier": "name"},
    ),
)


class TreeSitterParser(LanguageParser):
    def __init__(self, spec: LanguageSpec) -> None:
        self.spec = spec
        self.language = spec.language
        self.extensions = spec.extensions
        try:
            self._lang = Language(_load(spec.grammar))
        except TypeError:  # pragma: no cover - legacy tree-sitter 0.21
            self._lang = Language(_load(spec.grammar), spec.language)  # type: ignore[call-overload]
        try:
            self._parser = Parser(self._lang)
        except TypeError:  # pragma: no cover - legacy tree-sitter 0.21
            self._parser = Parser()
            self._parser.set_language(self._lang)  # type: ignore[attr-defined]

    # -- helpers -------------------------------------------------------------

    def _text(self, node, src: bytes) -> str:
        return src[node.start_byte : node.end_byte].decode("utf-8", "replace")

    def _unwrap(self, node):
        if node.type in self.spec.wrapper_types:
            decl = node.child_by_field_name("declaration")
            if decl is not None:
                return decl
            for c in node.named_children:
                if (c.type in self.spec.class_types
                        or c.type in self.spec.function_types
                        or c.type in self.spec.var_decl_types):
                    return c
        return node

    def _body_of(self, node):
        for f in self.spec.body_fields:
            body = node.child_by_field_name(f)
            if body is not None:
                return body
        for c in node.named_children:  # rust impl/mod: declaration_list child
            if c.type in ("declaration_list", "class_body", "field_declaration_list"):
                return c
        return None

    def _is_def(self, node) -> bool:
        n = self._unwrap(node)
        if n.type in self.spec.class_types or n.type in self.spec.function_types \
                or n.type in self.spec.group_types:
            return True
        if n.type in self.spec.var_decl_types:
            return any(self._fn_declarators(n))
        return False

    def _fn_declarators(self, decl_node):
        for c in decl_node.named_children:
            if c.type == "variable_declarator":
                value = c.child_by_field_name("value")
                if value is not None and value.type in self.spec.fn_value_types:
                    yield c, value

    # -- parse ---------------------------------------------------------------

    def parse(self, module_qualified_name: str, source: str) -> ParseResult:
        src = source.encode("utf-8")
        root = self._parser.parse(src).root_node
        symbols: list[ParsedSymbol] = []
        relationships: list[ParsedRelationship] = []
        counter = _Counter()

        header_nodes = [c for c in root.named_children if not self._is_def(c)]
        header_src = "\n".join(self._text(n, src) for n in header_nodes)
        mod_end = header_nodes[-1].end_point[0] + 1 if header_nodes else 1
        mod_name = module_qualified_name.rsplit(".", 1)[-1]
        module_sym = ParsedSymbol(
            local_id=counter.next(), parent_local_id=None,
            symbol_name=mod_name, qualified_name=module_qualified_name,
            symbol_type=MODULE, start_line=1, end_line=mod_end,
            source_code=header_src, signature=None, docstring=None,
            search_terms=build_search_terms(
                split_identifier(mod_name), extract_identifiers(header_src)),
        )
        symbols.append(module_sym)

        for node in header_nodes:
            if node.type in self.spec.import_types:
                for target in self._imports(node, src):
                    relationships.append(ParsedRelationship(
                        module_sym.local_id, IMPORTS, target, confidence=0.8))

        def emit_symbol(node, inner, name: str, kind: str,
                        parent_id: int, prefix: str) -> ParsedSymbol:
            qualified = f"{prefix}.{name}"
            body = self._body_of(inner)
            sig_end = body.start_byte if body is not None else inner.end_byte
            signature = (src[inner.start_byte:sig_end]
                         .decode("utf-8", "replace").strip().rstrip("{").strip()
                         or None)
            source_code = self._text(node, src)
            sym = ParsedSymbol(
                local_id=counter.next(), parent_local_id=parent_id,
                symbol_name=name, qualified_name=qualified, symbol_type=kind,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                source_code=source_code, signature=signature, docstring=None,
                search_terms=build_search_terms(
                    split_identifier(name),
                    [p for seg in qualified.split(".") for p in split_identifier(seg)],
                    extract_identifiers(signature or ""),
                    extract_identifiers(source_code)),
            )
            symbols.append(sym)
            return sym

        def emit_calls(sym: ParsedSymbol, body) -> None:
            for target, via in self._calls(body, src):
                relationships.append(ParsedRelationship(
                    sym.local_id, CALLS, target, confidence=0.6,
                    metadata={"via": via}))

        def walk(node, parent_id: int, prefix: str, in_class: bool) -> None:
            n = self._unwrap(node)

            if n.type in self.spec.var_decl_types:
                for declarator, value in self._fn_declarators(n):
                    name_node = declarator.child_by_field_name("name")
                    if name_node is None:
                        continue
                    sym = emit_symbol(node, value,
                                      self._text(name_node, src),
                                      METHOD if in_class else FUNCTION,
                                      parent_id, prefix)
                    body = self._body_of(value) or value
                    emit_calls(sym, body)
                return

            if n.type in self.spec.group_types:
                # Go type_declaration / Rust impl+mod: group only, no symbol
                # of its own (mod gets one so its children nest sensibly).
                if n.type == "mod_item":
                    name_node = n.child_by_field_name("name")
                    if name_node is None:
                        return
                    sym = emit_symbol(node, n, self._text(name_node, src),
                                      MODULE, parent_id, prefix)
                    body = self._body_of(n)
                    if body is not None:
                        for c in body.named_children:
                            if self._is_def(c):
                                walk(c, sym.local_id, sym.qualified_name, False)
                    return
                if n.type == "impl_item":
                    type_node = n.child_by_field_name("type")
                    impl_name = (self._text(type_node, src)
                                 if type_node is not None else "impl")
                    body = self._body_of(n)
                    if body is not None:
                        for c in body.named_children:
                            if self._is_def(c):
                                walk(c, parent_id, f"{prefix}.{impl_name}", True)
                    return
                for c in n.named_children:  # go type_declaration -> type_specs
                    if self._is_def(c):
                        walk(c, parent_id, prefix, in_class)
                return

            name_node = n.child_by_field_name("name")
            if name_node is None:
                return
            name = self._text(name_node, src)
            is_class = n.type in self.spec.class_types
            kind = CLASS if is_class else (METHOD if in_class else FUNCTION)
            # Go: methods declare a receiver, not an enclosing class
            if n.type == "method_declaration" and self.spec.language == "go":
                kind = METHOD
            sym = emit_symbol(node, n, name, kind, parent_id, prefix)

            body = self._body_of(n)
            if body is None:
                return
            if not is_class:
                emit_calls(sym, body)
            for c in body.named_children:
                if self._is_def(c):
                    walk(c, sym.local_id, sym.qualified_name, is_class)

        for child in root.named_children:
            if self._is_def(child):
                walk(child, module_sym.local_id, module_qualified_name, False)

        return ParseResult(symbols=symbols, relationships=relationships)

    # -- imports & calls -----------------------------------------------------

    def _imports(self, node, src: bytes) -> list[str]:
        out: list[str] = []

        def strings(n):
            for c in n.named_children:
                if c.type in ("string", "interpreted_string_literal",
                              "raw_string_literal"):
                    yield self._text(c, src).strip("\"'`")
                yield from strings(c)

        if self.spec.language in ("javascript", "typescript", "tsx"):
            source = node.child_by_field_name("source")
            if source is not None:
                out.append(self._text(source, src).strip("\"'`"))
        elif self.spec.language == "go":
            out.extend(strings(node))
        elif self.spec.language == "java":
            for c in node.named_children:
                if c.type in ("scoped_identifier", "identifier"):
                    out.append(self._text(c, src))
        elif self.spec.language == "rust":
            arg = node.child_by_field_name("argument")
            if arg is not None:
                out.append(self._text(arg, src))
        return out

    def _calls(self, body, src: bytes) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()

        def visit(node):
            for child in node.children:
                if self._is_def(child) and child.type not in self.spec.var_decl_types:
                    continue  # nested symbol owns its calls
                if child.type == self.spec.call_type:
                    hit = self._call_target(child, src)
                    if hit and hit not in seen:
                        seen.add(hit)
                        out.append(hit)
                visit(child)

        visit(body)
        return out

    def _call_target(self, call_node, src: bytes) -> tuple[str, str] | None:
        fn = call_node.child_by_field_name(self.spec.call_fn_field)
        if fn is None:
            return None
        if fn.type == "identifier":
            via = "attr" if call_node.child_by_field_name("object") is not None \
                else "bare"
            return (self._text(fn, src), via)
        name_field = self.spec.member_types.get(fn.type)
        if name_field is not None:
            name_node = fn.child_by_field_name(name_field)
            if name_node is not None:
                return (self._text(name_node, src), "attr")
        return None


class _Counter:
    def __init__(self) -> None:
        self._n = -1

    def next(self) -> int:
        self._n += 1
        return self._n
