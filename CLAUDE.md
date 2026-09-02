# CodeRAG — project notes for Claude Code

## Code search

This repo can be indexed in CodeRAG (`coderag index . --name coderag`). When the
`coderag` MCP server is connected, call `coderag_context` or `coderag_search`
**before** globbing/grepping or reading whole files — they return only the
relevant symbols and cost far fewer input tokens.

- `coderag_search` — locate symbols ("where is X defined?"). Returns ranked
  symbols with file, line range, and why each matched.
- `coderag_context` — understand behaviour ("why does X happen?"). Returns the
  target symbol plus its dependencies, callers, and tests, under a token budget.
- `coderag_symbol` — fetch one symbol's full source by qualified name.

Read whole files only when the retrieved context is genuinely insufficient. If
results reference code that has moved, re-index with `coderag_index`
(`incremental: true`).

## Working in this repo

- Run the gate before committing: `ruff check src tests scripts`, `mypy src/coderag`,
  `pytest`. All three must pass.
- Tests use `pgserver` — a real, rootless PostgreSQL + pgvector. No Docker needed,
  and no SQLite stand-ins: DB, full-text, and vector paths are exercised for real.
- Ranking weights and budgets live in `coderag.core.config.Settings`, never as
  literals scattered through the code.
- Never log full source, full prompts, secrets, or credentials. Every retrieval
  query must be scoped by `repository_id`.
- Don't claim a token saving that isn't measured. `--measure` and the eval harness
  exist so numbers come from a run, not an estimate.

## Publishing

Distribution name is `coderag-ai` on PyPI; the CLI, MCP binary, and import name
are all `coderag`. Releases publish via Trusted Publishing on a `v*` tag — bump
`version` in `pyproject.toml`, then `git tag -a vX.Y.Z && git push origin vX.Y.Z`.
