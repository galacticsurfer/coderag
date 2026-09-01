# Contributing to CodeRAG

Thanks for your interest! CodeRAG is Apache-2.0 licensed and built to be extended.

## Development setup

```bash
make install     # venv + dev deps (includes pgserver, so no Docker needed for tests)
make migrate     # apply migrations (needs a running Postgres; see docker-compose.yml)
make test        # full suite (spins up a rootless pgserver automatically)
make lint typecheck
```

Tests use [`pgserver`](https://pypi.org/project/pgserver/), which bundles a PostgreSQL
build with pgvector and runs without root or Docker. `@pytest.mark.db` marks tests that
need it; `make test-fast` skips them.

## Ground rules

- **Every change ships with tests.** No `pass`/`TODO`/`return []` stubs except declared,
  interface-only extension points.
- **Run `make lint typecheck test` before opening a PR.**
- **Security:** never log full source code, prompts, secrets, or credentials. Keep all
  retrieval queries scoped by `repository_id`. See [`SECURITY.md`](SECURITY.md).
- **No proprietary code.** Do not copy from Sourcegraph, Cursor, Cody, or similar. Build on
  public/OSS libraries only.
- **Configuration over constants.** Ranking weights and budgets live in
  `coderag.core.config.Settings`, not scattered literals.

## Extending

- New language parser → implement `coderag.parsing.base.LanguageParser`; see
  [`docs/adding-language.md`](docs/adding-language.md).
- New LLM transport → implement `coderag.llm.base.LLMProvider`; see
  [`docs/llm-providers.md`](docs/llm-providers.md).
- New embedding model → implement `coderag.embeddings.base.EmbeddingProvider`.

## Commit / PR conventions

Small, focused commits. Reference the phase or ADR where relevant. Describe *why*, not just
*what*.
