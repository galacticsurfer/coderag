# ADR-004: Tree-sitter as the parsing abstraction

- **Status:** Accepted
- **Context:** We need structural parsing across many languages eventually, with resilience to
  syntax errors, without maintaining a parser per language ourselves.
- **Decision:** Use Tree-sitter behind a `LanguageParser` interface. Ship a `PythonParser`
  first; other languages are interface stubs until implemented. Tree-sitter gives concrete
  syntax trees, error recovery, and a uniform node API across grammars.
- **Consequences:**
  - One extraction model across languages; adding a language = a grammar + a mapping from its
    node types to our symbol types.
  - Error-tolerant parsing (partial files still yield symbols).
  - Tree-sitter gives *syntax*, not full semantics — so relationship extraction is
    deliberately conservative (see ADR-006 / retrieval docs).
- **Revisit when:** a language needs semantic resolution beyond syntax (e.g. type-based call
  resolution) — layer a language-specific analyzer above the parser, keep the interface.
