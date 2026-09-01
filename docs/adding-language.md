# Adding a language

CodeRAG parses via Tree-sitter behind the `LanguageParser` interface
(`coderag/parsing/base.py`). Only Python is implemented in the MVP; the architecture is
language-independent.

## Steps

1. **Add the grammar dependency**, e.g. `tree-sitter-javascript`, and load it in your parser.
2. **Implement `LanguageParser`:**
   ```python
   class JavaScriptParser(LanguageParser):
       language = "javascript"
       extensions = (".js", ".mjs", ".cjs")

       def parse(self, path: str, source: str) -> list[ParsedSymbol]:
           ...
       def extract_relationships(self, symbols, source) -> list[ParsedRelationship]:
           ...
   ```
   Map the grammar's node types (e.g. `function_declaration`, `class_declaration`,
   `method_definition`) to CodeRAG symbol types (`module`/`class`/`function`/`method`).
3. **Register it** in `coderag/parsing/registry.py` so the indexer selects it by extension.
4. **Extract only syntax-reliable relationships** first (CONTAINS/IMPORTS/INHERITS, plus
   CALLS/REFERENCES by name). Give uncertain edges a lower `confidence`. An incomplete but
   correct graph beats a large wrong one.
5. **Add fixtures + tests** mirroring `tests/` for Python: symbols extracted, qualified names,
   line ranges, relationships, and re-index/delete behavior.

## Notes

- Chunk boundaries must follow constructs — never split a function mid-body (ADR-002).
- Docstring/comment extraction is language-specific; keep it in the parser.
