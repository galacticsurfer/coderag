# MCP setup example (Claude Code)

A complete, copy-pasteable setup, plus **real** tool output captured from the
bundled demo repository — not illustrative JSON.

Files here:

| File | Use |
|---|---|
| [`.mcp.json`](.mcp.json) | Drop in your project root (project-scoped server, committable) |
| [`CLAUDE.md`](CLAUDE.md) | Append to your project's `CLAUDE.md` — **this is what makes Claude Code actually use the tools** |

---

## 1. Install and start the database

```bash
pipx install "coderag-ai[mcp,localdb]"
coderag localdb start            # prints the DB URL; applies migrations
export CODERAG_DATABASE_URL='…'  # paste what it printed; add to ~/.zshrc
```

## 2. Index the repo you want to search

```bash
cd /path/to/your-project
coderag index . --name myproject
coderag search "where is authentication handled?"   # sanity check, no LLM needed
```

## 3. Register the server — pick one

**A. One command, all projects:**

```bash
claude mcp add coderag \
  -e CODERAG_DATABASE_URL="$CODERAG_DATABASE_URL" \
  -e CODERAG_DEFAULT_REPOSITORY=myproject \
  -s user \
  -- "$(which coderag-mcp)"
```

**B. Committed `.mcp.json`** (see the file in this directory) so your team gets it
automatically. Note JSON has no shell expansion — paste literal values.

Either way: use the **absolute path** to `coderag-mcp`. Claude Code spawns it
without your shell's PATH, so a bare command name fails with "command not found".

## 4. Add the `CLAUDE.md` nudge

Append [`CLAUDE.md`](CLAUDE.md) to your project's `CLAUDE.md`. **Without it the
saving is zero** — the tools are registered but unused.

## 5. Verify

Run `/mcp` in Claude Code. You should see `coderag` **connected** with five tools.
Then just ask a question normally — MCP tools are not slash commands, so there is
no `/coderag`:

> Where are CPQ filter options defined across this repo?

To force a tool on the first try, name it: *"Use the coderag_search tool to …"*.

---

## What the tools actually return

Captured by running the tools against `examples/demo-repository`.

### `coderag_search("where are failed payments retried?", limit=3)`

```json
[
  {
    "symbol": "payments.payment_service.PaymentService.retry_payment",
    "type": "method",
    "file": "payments/payment_service.py",
    "lines": "33-54",
    "score": 0.04672,
    "why": ["graph_callee", "graph_caller", "graph_child", "lexical", "semantic"],
    "tokens": 215
  }
]
```

Every hit explains **why** it was retrieved — `lexical` (full-text), `semantic`
(vector), `exact_symbol`, or a graph reason such as `graph_caller` / `graph_test`.

### `coderag_context("why can retry_payment leave an invoice pending?")`

Returns `context` (the code text), `selected_symbols`, `token_accounting`, and a
plain-language `measurement`:

```
Returned 1449 tokens of context instead of the 1654 tokens it would take to read
the 8 file(s) containing this code (12.4% fewer input tokens).
```

```json
{
  "candidates_found": 38,
  "candidates_selected": 27,
  "candidate_tokens": 2240,
  "context_tokens": 1449,
  "token_reduction_from_candidates": 35.3,
  "baseline_tokens": 1654,
  "baseline_files": 8,
  "tokens_saved_vs_files": 205,
  "reduction_vs_files": 12.4
}
```

Symbols come back grouped by priority — target first, then dependencies, callers,
tests:

```json
[
  {"category": "target",          "symbol": "…PaymentService.retry_payment",   "lines": "33-54", "tokens": 215},
  {"category": "implementation",  "symbol": "…PaymentService.__init__",        "lines": "13-21", "tokens": 51},
  {"category": "dependencies",    "symbol": "…PaymentService.process_payment", "lines": "23-31", "tokens": 75}
]
```

> **Note the 12.4%.** That's the *demo* repo — 11 tiny files, where retrieval
> reaches most of the codebase and the saving is small. On a real backend the
> same measurement runs far higher (75% has been observed). Run
> `coderag context "…" --measure` on your own repo for your number; don't trust
> either figure as representative.

---

## The five tools

| Tool | Use |
|---|---|
| `coderag_context` | Budgeted context for a question (target + deps + callers + tests) |
| `coderag_search` | Ranked symbols with file/lines/score/why |
| `coderag_symbol` | Full source of one symbol by qualified name |
| `coderag_repositories` | Which repos are indexed, and at which commit |
| `coderag_index` | Index / re-index (`incremental: true` for a fast update) |

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `/mcp` doesn't list `coderag` | Server not registered, or `.mcp.json` is not in the project root. Reload the window. |
| Listed but **failed** | Run the command by hand: `CODERAG_DATABASE_URL=… /abs/path/coderag-mcp`. A healthy server sits silently on stdin (Ctrl-C to exit); a traceback tells you what's wrong. |
| "command not found" | Use the absolute path from `which coderag-mcp`. |
| "no repositories indexed yet" | Run `coderag index . --name myproject`. |
| "multiple repositories; pass --repo" | Set `CODERAG_DEFAULT_REPOSITORY`. |
| Tools connected but never called | The `CLAUDE.md` nudge is missing — see step 4. |
| Results reference code that moved | Re-index: `coderag index . --name myproject --incremental`. |
