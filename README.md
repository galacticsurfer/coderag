# CodeRAG

**Token-efficient, structure-aware Code Intelligence + RAG for private source repositories.**

CodeRAG indexes private source-code repositories, retrieves *only the code relevant* to a
developer's request, builds a token-budgeted context package, and sends it to your
organisation's Claude endpoint (or any LLM behind the `LLMProvider` interface).

> **Primary objective:** reduce LLM input-token consumption **without** reducing answer
> quality — and *prove it with measurements*.

This is not a generic document-RAG app: chunks are functions/methods/classes/modules
(via Tree-sitter), retrieval is hybrid (exact-symbol + Postgres full-text + pgvector
semantic + a lightweight code graph), and every result explains *why* it was retrieved.

See [`docs/`](docs/) for the architecture, retrieval design, security model, and evaluation
methodology. A detailed quick-start lands as the phases complete; the short version:

```bash
cp .env.example .env
docker compose up -d           # PostgreSQL + pgvector
make install
make migrate
coderag index ./examples/demo-repository
coderag search "where are failed payments retried?"     # no LLM needed
coderag context "why can payment retry leave an invoice pending?"   # no LLM needed
coderag ask "why can payment retry leave an invoice pending?"       # needs an LLM
```

Retrieval (`search`, `context`) works **without any LLM credentials**. Only `ask` needs a
provider. Embeddings run **locally** by default (no source code leaves your infra).

## Status

Built incrementally, phase by phase, with tests at every step. See `docs/` and the ADRs in
`docs/adr/`. Licensed under Apache-2.0.
