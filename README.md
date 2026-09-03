# CodeRAG

**Token-efficient, structure-aware Code Intelligence + RAG for private source repositories.**

CodeRAG indexes private source-code repositories, retrieves *only the code relevant* to a
developer's request, builds a **token-budgeted** context package, and sends it to your
organisation's Claude endpoint (or any LLM behind the `LLMProvider` interface).

> **Primary objective:** reduce LLM input-token consumption **without** reducing answer
> quality — and *prove it with measurements*, never claims.

This is **not** a generic document-RAG app:

- **Chunks are code constructs** — functions/methods/classes/modules — via Tree-sitter, never
  fixed-size text slices.
- **Retrieval is hybrid**: exact-symbol + PostgreSQL full-text (lexical) + pgvector (semantic)
  + a lightweight one-hop code graph, fused with Reciprocal Rank Fusion.
- **Every result explains *why*** it was retrieved (`exact_symbol`, `lexical`, `semantic`,
  `graph_caller`, `graph_test`, …).
- **Embeddings run locally** by default — no source code leaves your infrastructure.
- **Retrieval works with no LLM.** Only `ask` needs a provider.

## What problem this solves

Instead of pasting whole files (or a whole repo) into a prompt, CodeRAG retrieves the smallest
useful set of symbols for a task and packs them under a hard token budget, deduplicating
overlapping code and dropping low-priority symbols before anything is sent externally. Token
consumption is measured end-to-end and comparable against a naive baseline.

## Architecture

```mermaid
flowchart LR
  G[Git repo] --> IDX[Indexer]
  IDX --> TS[Tree-sitter parse] --> SX[Symbols + graph]
  SX --> PG[(PostgreSQL + pgvector)]
  EMB[Local embeddings] --> PG
  Q[Query] --> SYM[symbol] & LEX[full-text] & SEM[vector]
  PG --- SYM & LEX & SEM
  SYM & LEX & SEM --> RRF[RRF fusion] --> EXP[1-hop graph expand]
  EXP --> RR[reranker*] --> CB[Context builder + token budget] --> LLM[LLMProvider] --> C[Claude]
```

Full details: [`docs/architecture.md`](docs/architecture.md), plus ADRs in
[`docs/adr/`](docs/adr/) and topic docs for [retrieval](docs/retrieval.md),
[security](docs/security.md), [evaluation](docs/evaluation.md),
[LLM providers](docs/llm-providers.md), and [adding a language](docs/adding-language.md).

## Install

**Two commands.** Ships a rootless bundled PostgreSQL+pgvector — no Docker, Homebrew, or `sudo`:

```bash
pipx install "coderag-ai[mcp,localdb]"
cd /path/to/your-project && coderag setup
```

`coderag setup` starts the database, applies migrations, indexes the repo, registers the MCP
server with Claude Code, and appends the retrieval nudge to your `CLAUDE.md` — the step that
makes Claude Code actually call the tools. It records the database URL, so **no
`export CODERAG_DATABASE_URL` is needed**; later commands find it automatically. Re-running is
safe (it won't duplicate the nudge). Opt out with `--no-mcp` / `--no-claude-md`.

```bash
coderag search "where is authentication handled?"   # works in any new shell
```

Full walkthrough incl. Claude Code wiring: [`docs/install-macos.md`](docs/install-macos.md).

Other install forms:

```bash
# from PyPI
pip install coderag-ai
# with local semantic embeddings (pulls torch, ~2GB)
pip install "coderag-ai[embeddings]"
# straight from git (no PyPI needed)
pipx install "coderag-ai[mcp,localdb] @ git+https://github.com/galacticsurfer/coderag"
# or clone + editable
git clone https://github.com/galacticsurfer/coderag && cd coderag && pip install -e ".[dev]"
```

> **Distribution name is `coderag-ai`** (plain `coderag` was taken on PyPI by an unrelated
> project). The CLI command, the MCP binary, and the import name are all still `coderag`.

This installs the `coderag` CLI and the `coderag.api.app` FastAPI app. Extras: `embeddings`
(SentenceTransformers), `tokens` (tiktoken), `analyzers` (pylint/flake8), `metrics`
(prometheus). You still need a PostgreSQL+pgvector to point `CODERAG_DATABASE_URL` at
(`docker compose up -d` provides one).

## Quick start

### Option A — Docker one-shot (recommended)

One command boots PostgreSQL+pgvector **and** the API, runs migrations, and indexes the bundled
demo repo so the dashboard has data immediately:

```bash
make up          # == docker compose up -d --build
# open http://localhost:8000/dashboard   and   http://localhost:8000/docs
```

Index *your own* code (mount it, then run the indexer inside the container):

```bash
CODERAG_REPO_PATH=/abs/path/to/your/repo make up
docker compose exec api coderag index /workspace --name myrepo
docker compose exec api coderag search "where are failed payments retried?" --repo myrepo
```

### Option B — Local (venv)

```bash
cp .env.example .env
docker compose up -d db                         # just Postgres+pgvector
make install && make migrate
coderag index ./examples/demo-repository
coderag search  "where are failed payments retried?"                # no LLM needed
coderag context "why can payment retry leave an invoice pending?"   # no LLM needed
coderag eval                                                        # Recall@K, MRR, tokens
coderag benchmark --compare-baseline                                # measured token savings
coderag ask     "why can payment retry leave an invoice pending?"   # needs an LLM (see below)
```

### Do I need an API key?

**No — for almost everything.** `index`, `search`, `context`, `eval`, `benchmark`, and the
`/dashboard` are pure retrieval + local embeddings + telemetry: **no key, nothing leaves your
box.** Only **`ask`** calls an LLM to turn the retrieved context into a natural-language answer,
so only `ask` needs `CODERAG_ANTHROPIC_API_KEY` (or an internal proxy / Bedrock — see
[`docs/llm-providers.md`](docs/llm-providers.md)). That separation is the whole point: you can
run, measure, and demo the token savings with zero LLM credentials.

## Indexing

```bash
coderag index /path/to/repo --name myrepo     # full index
coderag index /path/to/repo --incremental     # only reindex what changed since last commit
```

Respects `.gitignore`; skips vendored/generated/binary files and secret files (`.env`,
`*.pem`, keys, …); redacts residual secret-shaped strings; never indexes credentials.
Incremental indexing preserves embeddings for unchanged code.

## Searching & context

```bash
coderag search "retry failed payment" --repo myrepo --limit 10
coderag context "why pending?" --show-prompt          # exact prompt + token accounting
```

`coderag context` prints the selected symbols by priority (target → dependencies → callers →
tests → semantic → additional), the token accounting, and (optionally) the full prompt — so
you can debug token usage without calling the model.

## Claude configuration

Set in `.env` (never commit credentials):

```
CODERAG_LLM_PROVIDER=anthropic
CODERAG_ANTHROPIC_API_KEY=sk-...
CODERAG_ANTHROPIC_BASE_URL=https://api.anthropic.com   # or your internal proxy
CODERAG_ANTHROPIC_MODEL=claude-sonnet-5
```

The provider talks to the Messages API over HTTP, so pointing it at an internal proxy — or
subclassing for AWS Bedrock — needs no changes to the retrieval layer
([`docs/llm-providers.md`](docs/llm-providers.md)).

## Embedding configuration

```
CODERAG_EMBEDDING_PROVIDER=hashing            # offline default (deterministic)
# or, for genuine semantics (installs torch): pip install 'coderag-ai[embeddings]'
CODERAG_EMBEDDING_PROVIDER=sentence_transformer
CODERAG_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
```

The default `hashing` embedder is offline and deterministic (great for CI/air-gapped dev);
similarity reflects shared vocabulary. Switch to the local SentenceTransformers model for true
semantic matching — code still never leaves your infra.

## Evaluation & token measurement

```bash
coderag eval --dataset examples/eval/eval_dataset.json
coderag benchmark --compare-baseline
```

On the bundled demo (offline `hashing` embedder), measured:

| metric | value |
|--------|-------|
| Recall@1 / @3 / @5 / @10 | 0.375 / 0.625 / 0.875 / **1.000** |
| MRR | 0.574 |
| search latency p50 / p95 | ~6 ms / ~9 ms |
| context reduction vs whole-repo baseline | ~13% |

### Measure it on your own repo (`--measure`)

```bash
coderag context "why can payment retry leave an invoice pending?" --measure
```

reports the counterfactual an agent actually faces — *"instead of this context I'd have opened
the files"* — using per-file token counts recorded at index time:

| approach | input tokens |
|---|---|
| read the 8 whole files containing this code | 1,654 |
| CodeRAG budgeted context | 1,516 |
| CodeRAG full prompt (incl. scaffolding) | 2,648 |

**Read that honestly: on the bundled demo that's only 8.3% saved, and the full prompt is bigger
than the files it replaces.** That's a real measurement, not a bug — with 11 tiny files, one-hop
expansion reaches most of the repo and fixed scaffolding (~1.1k tokens) dominates. Savings scale
with *file size ÷ symbol size*: pulling one 40-line method out of a 900-line service is where
this wins, and that ratio is routine on a real backend. Recall@1 is likewise limited by the
offline hashing embedder; the local SentenceTransformers model improves ranking.

**So run `--measure` on your repository before believing any headline number, including ours.**
That's why the measurement ships instead of a claim. See
[`docs/evaluation.md`](docs/evaluation.md).

## Security model

Source code is treated as sensitive: secrets are never indexed, credentials are redacted, full
source/prompts are never logged, every retrieval query is scoped by `repository_id` (cross-repo
isolation is a test, not a hope), and an `AuthorizationProvider` interface gates repository
access. Retrieved code is delimited as untrusted **data** in the prompt to mitigate injection.
See [`SECURITY.md`](SECURITY.md) and [`docs/security.md`](docs/security.md).

## Limitations (MVP)

- **Python only** (architecture is language-independent; other parsers are interface stubs).
- **Graph is syntax-heuristic**: only in-repo, confidently-resolved edges are stored
  (incomplete-but-correct by design). Ambiguous/external calls are dropped.
- **Exact vector scan** (no HNSW yet — added when dataset size warrants).
- **Token counts are estimates** for budgeting; authoritative counts come from the LLM's usage
  report (stored in `llm_requests`).
- Default `hashing` embedder is lexical-overlap, not truly semantic.
- Analyzer workflow produces **fix context + proposals**; patch apply/verify is a caller
  interface (bounded by `MAX_FIX_ATTEMPTS`).
- The MVP `AuthorizationProvider` is permissive (development). Deploy a real one.

## API

`uvicorn coderag.api.app:app` exposes `POST /repositories`, `/repositories/{id}/index`,
`GET /repositories/{id}/index/status`, `POST /search`, `POST /context`, `POST /ask`,
`GET /symbols/{id}`, `/symbols/{id}/relationships`, `GET /metrics`, `GET /queries`, and a
`GET /dashboard` page.

## Dashboard

A self-contained observability page at **`/dashboard`** (no external assets, light/dark) shows
*what was queried and how many tokens the budgeted context saved* — KPI tiles (tokens saved,
overall reduction, LLM tokens), a per-query "context vs. saved" bar chart, and a full query log.
It reads the persisted `queries`/`llm_requests` telemetry via `GET /metrics` and `GET /queries`.

```bash
uvicorn coderag.api.app:app --port 8000   # then open http://localhost:8000/dashboard
```

## Development

```bash
make lint typecheck test      # ruff + mypy + pytest
```

Tests use [`pgserver`](https://pypi.org/project/pgserver/) — a rootless, bundled
PostgreSQL+pgvector — so the DB/FTS/vector paths are exercised for real, no Docker required.

Licensed under **Apache-2.0** (see [`LICENSE`](LICENSE) / [`NOTICE`](NOTICE)). Dependency
licenses (incl. the psycopg LGPL and pylint GPL flags) are in
[`docs/licenses.md`](docs/licenses.md).

## Use it to cut Claude Code's token usage (MCP)

Claude Code reads whole files to build context. Register CodeRAG as an **MCP server** and it can
call `coderag_context` / `coderag_search` instead — getting only the budgeted, relevant symbols:

```bash
pipx install "coderag-ai[mcp]"
claude mcp add coderag \
  --env CODERAG_DATABASE_URL=postgresql+psycopg://coderag:coderag@localhost:5432/coderag \
  --env CODERAG_DEFAULT_REPOSITORY=myrepo -- coderag-mcp
```

Tools: `coderag_context`, `coderag_search`, `coderag_symbol`, `coderag_repositories`,
`coderag_index`. Every call is recorded, so `/dashboard` shows the savings live. No API key
needed — Claude Code is the LLM. **Full step-by-step:**
[`docs/claude-code.md`](docs/claude-code.md). Copy-pasteable `.mcp.json` + `CLAUDE.md` snippet,
with **real** captured tool output: [`examples/mcp/`](examples/mcp/).

## The `/token-lean` skill

`coderag setup` also installs a Claude Code **skill** at
`~/.claude/skills/token-lean/` (skip with `--no-skill`). It covers both levers:

- **Input** — prefer `coderag_search` / `coderag_context` over Glob/Grep/Read, with explicit
  guidance on when reading whole files is still the right call.
- **Output** — lead with the outcome, don't restate the request, don't echo visible code, don't
  narrate routine tool calls, no closing offer-lists. Output is billed at ~5× input, so this
  half usually matters more.

It loads automatically when a task looks token-sensitive, or invoke it explicitly with
`/token-lean`.

**Be clear about what it is:** a set of instructions, not machinery. It cannot compress a
prompt, place cache breakpoints, or route to a cheaper model — those need a proxy or gateway. A
skill is also *probabilistic* (the model decides to apply it), where a proxy is deterministic.
Its effect is **not measured by CodeRAG**, which only sees its own retrieval, never the host
agent's total usage. Check your client's own cost reporting.

## The observability proxy — measure real usage, not estimates

Everything above measures CodeRAG's *own* retrieval. Your agent's actual LLM traffic (Claude
Code's real billed tokens) never passes through CodeRAG — until you run the proxy:

```bash
coderag proxy                                    # listens on 127.0.0.1:8788
export ANTHROPIC_BASE_URL=http://127.0.0.1:8788  # point your agent at it
```

Every request is forwarded **byte-for-byte unmodified** — headers, body, streaming included.
The proxy does exactly one extra thing: it reads the `usage` numbers out of responses and
records them, so the dashboard's LLM input/output tiles show **provider-billed tokens** instead
of zeros. That's what turns "estimated savings" into a before/after you can actually check.

Hard guarantees, enforced by tests:
- **No compression, rewriting, caching, or routing.** Anything that changes bytes can change
  model behaviour; this proxy never does. (Byte-fidelity is asserted in the test suite,
  streaming included.)
- **No prompts, responses, or credentials are ever stored** — only token counts, model,
  latency, and status.
- **A database failure cannot break your traffic** — recording is fire-and-forget.
- Binds to loopback only by default.

It chains: `--upstream http://127.0.0.1:8787` forwards to another proxy (e.g. a compression
proxy) instead of the API, so you can observe *and* compress:
`agent → coderag proxy (measure) → compression proxy → api.anthropic.com`.

### Opt-in automatic cache placement (`--auto-cache`)

Claude Code places its own cache breakpoints — but raw SDK scripts and many agents don't, and
they bill an identical, growing prefix at the full input rate every turn when ~90% of it could
bill at the 0.1× cache-read rate. `coderag proxy --auto-cache` injects the standard placement
(last tool definition, last system block, last block of the final message) into requests that
contain **no** `cache_control` at all; a client that manages its own caching is never touched,
which also makes the transform idempotent. Metadata only — no prompt content is added, removed,
or reordered. The doctor's `no_caching` detector tells you when this flag would pay for itself.

### Opt-in model routing (`--route`)

```bash
coderag proxy --route claude-opus-4-8=claude-sonnet-5     # repeatable
```

The bluntest cost lever there is — a cheaper model is cheaper on every token — and the most
dangerous, because it changes answer quality more than any compressor. So the policy is
deliberately dumb: **you** name explicit `source=destination` pairs, the proxy rewrites exact
model-ID matches, and nothing else happens. No heuristics guessing task difficulty, no
automatic downgrades, off by default, loud warning when on. The proxy records the originally
requested model alongside the served one, so `coderag doctor` reports **measured** routing
savings (token counts x the published price difference) — and the `expensive_model_dominant`
detector tells you when routing is worth trying at all.

### Opt-in compression (`--compress`)

```bash
coderag proxy --compress
```

Adds a narrow, **cache-safe** compression layer for **tool-result blocks only** (logs, test
runs, build output) in request bodies. Three deterministic transforms — ANSI stripping,
consecutive-duplicate-line dedupe, blank-line squeeze — plus recoverable elision of oversized
blocks: the middle is cut, the head/tail kept, and the raw original stored locally
(`~/.coderag/proxy-cache/`, content-addressed). The marker names the key, and the agent can
fetch the exact original back with the `coderag_expand` MCP tool.

The transforms are content-aware and route by shape: **JSON tool results** get a
structure-preserving compressor (every object key survives, error-ish subtrees are never
shrunk, long arrays keep their edges, long strings truncate); **error/warning/traceback lines
survive elision** in logs (up to 40, in original order); **diffs are exempt from elision
entirely** (every changed line is signal); **oversized base64/hex blobs** are elided
recoverably. Everything stays deterministic and byte-exact recoverable.

Why the design is shaped this way:

- **Deterministic, or it costs you money.** The client resends original history every turn and
  the proxy recompresses it; prompt caching is a prefix match, so identical input must produce
  identical output or every turn invalidates the provider's ~0.1× cache discount. All
  transforms are pure functions — determinism is asserted in the test suite.
- **Tool results only.** System prompts, user text, assistant turns, and tool definitions are
  never touched.
- **Guarded.** Doesn't parse, doesn't shrink by a meaningful margin, or anything errors → the
  original bytes are forwarded untouched.
- **The byte-fidelity guarantee applies to observe-only mode.** `--compress` deliberately
  modifies request bytes; that's the trade. It is off by default for exactly that reason.
- Savings counters are on `GET /coderag-proxy/health`. Note the stored originals contain tool
  output from your machine — they stay local, in your home directory, and are never uploaded.

## `coderag doctor` — where is the money actually going?

Once the proxy has observed some traffic, ask the doctor:

```bash
coderag doctor
```

It attributes every observed dollar to one of four categories — **fresh input** (1× the input
price), **cache reads** (0.1×), **cache writes** (1.25×), **output** (typically 5× input) — and
then runs a rule engine over *your* traffic to rank the levers by estimated $ impact:

| Diagnosis | Fires when | Lever |
|---|---|---|
| Output-dominant spend | output ≥ 50% of observed cost | lower `effort`, terse-output rules (`/token-lean`) |
| Cache barely hit | large repeated inputs, hit rate < 40% | find what invalidates your prompt prefix |
| Context growing steeply | per-request input ≥ 2× across the window | `/compact`, fresh sessions per task |
| Retrieval unused | LLM traffic but no CodeRAG queries | check `/mcp` + the CLAUDE.md nudge |
| Tool output heavy | tool_result ≥ 20% of fresh input | `coderag proxy --compress` |

Every diagnosis cites the observed numbers it rests on and states the assumption behind its
estimate — because the honest answer is sometimes "this lever wouldn't help *you*". The same
report is on the dashboard (`/dashboard`) and as JSON at `GET /doctor`. All dollar figures are
estimates at the configured prices (`CODERAG_PRICE_INPUT_PER_MTOK` /
`CODERAG_PRICE_OUTPUT_PER_MTOK`), computed from observed traffic — not billing data.

### The output side, measured and (optionally) capped

Output tokens are billed as they are generated — nothing can compress them after the fact, so
every "output saver" (including ours) works by influencing what the model writes. CodeRAG does
two things no instruction-only approach can:

- **It measures whether the `/token-lean` skill actually works on your traffic.** The proxy
  flags each request where the skill is active (a byte-marker check — no content is parsed or
  stored) and the doctor compares average output tokens/request across the two groups. Once
  both groups have ≥5 requests, the doctor's output-savings estimate uses *your measured
  reduction* instead of an assumption — and if the skill makes output *longer*, it says so.
  The comparison is observational (tasks differ between groups), and is labelled as such.
- **Mechanical caps, opt-in, loudly labelled.** `coderag proxy --cap-output N` clamps
  `max_tokens` downward in every request; `--cap-thinking N` clamps an explicit extended-
  thinking `budget_tokens` (adaptive thinking is never touched; nothing is ever raised or
  invented). These are real valves, not polite notes — and they deliberately trade answer
  quality for cost, which is why they are off by default and the CLI warns when they're on.
  Capped-request counts are on `GET /coderag-proxy/health`.

## Composes with other token-efficiency tools

CodeRAG started on **one slice** of token spend — the code you'd otherwise read whole files to
get — and has since grown levers for most of the others. Where the current coverage stands
against tools like [Caveman](https://caveman.so):

| Slice | Billed at | CodeRAG | Tools like Caveman |
|---|---|:--:|:--:|
| File-reading input | 1× input | ✅ retrieval instead of whole files | — |
| Output tokens | **5× input** | ✅ `/token-lean` skill (effect *measured* per-workload) + opt-in `--cap-output`/`--cap-thinking` | ✅ output compression, fine-tuned models |
| Tool output in history | 1× / 0.1× | ✅ `--compress` (JSON/log/diff-aware, deterministic, recoverable) | ✅ more content shapes (HTML/code/prose, query-aware selection) |
| Cache placement | 0.1× reads | ✅ `--auto-cache` (standard breakpoints, never touches clients that already cache) | ✅ automatic breakpoints |
| Waste diagnosis | — | ✅ `coderag doctor` (10 detectors, each with evidence + stated assumptions) | ✅ (~20 detectors) |
| Model routing | varies | ✅ explicit `--route` pairs + **measured** savings | ✅ cheapest model passing evals |

Remaining honest gaps on our side: Caveman still routes more content shapes (HTML, code,
prose, with BM25 query-aware selection — lower value for coding-agent traffic, which is mostly
logs/JSON/diffs), ships ~20 detectors to our 10, and its routing picks models automatically
via evals where ours requires the user to name pairs (a deliberate choice: automatic
downgrades gamble with quality; explicit pairs + measured savings don't). Remaining gaps on
theirs: no retrieval (they shrink what's in the request; retrieval stops it entering), no
per-workload measured skill/routing effects, and their proxy is BSL-1.1 where this entire
stack is MIT.

Savings on disjoint slices are near-additive. On a typical mid-session turn, CodeRAG's measured
file-slice reduction works out to ~26% of cost; adding an output-compression layer takes the
combined figure to roughly ~38%. **Both halves of that are models, not measurements** — verify
on your own workload before believing either.

**Wiring:** they use different mechanisms and don't conflict — CodeRAG is an MCP server, a
compression skill is a Skill, a proxy is an `ANTHROPIC_BASE_URL`. CodeRAG's own `ask` can also
route through such a proxy, since the LLM base URL is configurable:

```bash
export CODERAG_ANTHROPIC_BASE_URL=http://localhost:<proxy-port>
```

Enable one at a time and compare `/cost` between them — a proxy that compresses context may be
compressing context CodeRAG already minimised, so the marginal gain can be smaller than the two
vendors' claims added together.

## Editor integration (VS Code)

A ready-to-build extension lives in [`vscode-extension/`](vscode-extension/) — commands for
**Index this workspace** (the indexing step, one click), **Search symbols**, **Show context**,
**Ask about this code** (right-click), and **Open token dashboard**.

```bash
cd vscode-extension && npm install && npm run package   # → coderag-vscode-0.1.0.vsix
code --install-extension coderag-vscode-0.1.0.vsix
```

Or download the prebuilt `.vsix` from the repo's
[Releases](https://github.com/galacticsurfer/coderag/releases). It's a thin client — point
`coderag.serverUrl` at your running CodeRAG server. Design notes:
[`docs/vscode-extension.md`](docs/vscode-extension.md).
