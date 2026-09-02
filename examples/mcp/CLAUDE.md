# Append this to your project's CLAUDE.md

Registering the MCP server is not enough on its own — Claude Code will keep
globbing, grepping, and reading whole files unless you tell it not to. This
snippet is what actually produces the token saving.

---

## Code search

This repo is indexed in CodeRAG. To find or understand code, call the
`coderag_context` or `coderag_search` MCP tools **before** globbing/grepping or
reading whole files — they return only the relevant symbols and cost far fewer
input tokens.

- `coderag_search` — locate symbols ("where is X defined?"). Returns ranked
  symbols with file, line range, and why each matched.
- `coderag_context` — understand behaviour ("why does X happen?"). Returns the
  target symbol plus its dependencies, callers, and tests, under a token budget.
- `coderag_symbol` — fetch one symbol's full source by qualified name.

Read whole files only when the retrieved context is genuinely insufficient. If
the index looks stale, call `coderag_index` with `incremental: true`.
