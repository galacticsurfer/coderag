# Step-by-step: cut Claude Code's token usage with CodeRAG (VS Code)

**The problem.** Claude Code finds context by globbing, grepping, and reading **whole files**.
On a large repo that's where your input tokens go — one "why does X happen?" can pull in
thousands of lines you didn't need.

**The fix.** Register CodeRAG as an **MCP server**. Claude Code then calls `coderag_context` /
`coderag_search` and gets back *only* the relevant symbols (target + dependencies + callers +
tests) under a hard token budget, instead of reading files.

> Honest framing: installing CodeRAG does **not** shrink Claude Code's usage on its own. The
> saving happens when Claude Code *calls these tools instead of reading files* — which is why
> Step 6 (the `CLAUDE.md` nudge) is not optional.

You do **not** need an Anthropic API key for any of this — Claude Code is your LLM. CodeRAG here
is pure retrieval.

---

## Step 1 — Start PostgreSQL + the API (one command)

```bash
git clone https://github.com/galacticsurfer/coderag
cd coderag
make up            # == docker compose up -d --build
```

This boots PostgreSQL+pgvector, applies migrations, and serves the API + dashboard on
**http://localhost:8000**. Leave it running.

## Step 2 — Install the CodeRAG CLI + MCP server

```bash
pipx install "coderag-ai[mcp]"
# or: pip install --user "coderag-ai[mcp]"
```

`pipx` is recommended — it puts `coderag` and `coderag-mcp` on your PATH in an isolated env,
which is exactly what Claude Code needs to spawn.

Check it worked:

```bash
which coderag-mcp && coderag --help
```

## Step 3 — Point it at the database

```bash
export CODERAG_DATABASE_URL=postgresql+psycopg://coderag:coderag@localhost:5432/coderag
```

(Put this in your shell profile so it persists.)

## Step 4 — Index your project *(this is the indexing step)*

```bash
coderag index /path/to/your-repo --name myrepo
```

You'll see something like `Indexed myrepo: 312 files, 4180 symbols, … relationships`.
Re-run after big changes — `coderag index /path/to/repo --incremental` only reprocesses what
changed since the last commit.

Sanity-check retrieval before wiring it into Claude Code:

```bash
coderag search "where is authentication handled?" --repo myrepo
```

## Step 5 — Register the MCP server with Claude Code

**Option A — one command (this project only):**

```bash
cd /path/to/your-repo
claude mcp add coderag \
  --env CODERAG_DATABASE_URL=postgresql+psycopg://coderag:coderag@localhost:5432/coderag \
  --env CODERAG_DEFAULT_REPOSITORY=myrepo \
  -- coderag-mcp
```

**Option B — commit `.mcp.json` in the project root so your whole team gets it:**

```json
{
  "mcpServers": {
    "coderag": {
      "command": "coderag-mcp",
      "env": {
        "CODERAG_DATABASE_URL": "postgresql+psycopg://coderag:coderag@localhost:5432/coderag",
        "CODERAG_DEFAULT_REPOSITORY": "myrepo"
      }
    }
  }
}
```

`CODERAG_DEFAULT_REPOSITORY` means the tools work without naming the repo every call.

**Verify:** open Claude Code in VS Code and run `/mcp` — `coderag` should be listed as
connected with five tools.

## Step 6 — Tell Claude Code to prefer it (the step that actually saves tokens)

Add this to the project's `CLAUDE.md`:

```markdown
## Code search
This repo is indexed in CodeRAG. To find or understand code, call the `coderag_context`
or `coderag_search` MCP tools **before** globbing/grepping or reading whole files —
they return only the relevant symbols and cost far fewer input tokens.
Use `coderag_symbol` to fetch one symbol's full source. Read whole files only when the
retrieved context is genuinely insufficient.
```

## Step 7 — Watch the savings

Open **http://localhost:8000/dashboard**. Every MCP tool call is recorded through the same
telemetry as the CLI/API, so you'll see tokens saved, overall reduction %, per-query
"context vs. saved" bars, and the full query log — updating as you work in Claude Code.

---

## Tools exposed

| Tool | Use |
|------|-----|
| `coderag_context` | Budgeted context for a question (target + deps + callers + tests). |
| `coderag_search` | Ranked symbols with file/lines/score/why. |
| `coderag_symbol` | Full source of one symbol by qualified name. |
| `coderag_repositories` | Which repos are indexed. |
| `coderag_index` | Index / re-index (supports `incremental`). |

## Releasing / distributing the MCP server

An MCP server isn't published to the VS Code Marketplace — it's just a **command** Claude Code
spawns. So "releasing" it means making that command installable. Options, easiest first:

1. **Install from GitHub (works today, nothing to publish):** Step 2 above. Team members run
   the same `pipx install …` line.
2. **Publish to PyPI** so it's `pipx install "coderag-ai[mcp]"`:
   ```bash
   python -m pip install build twine
   python -m build                     # dist/*.whl + *.tar.gz
   twine upload dist/*                 # needs a PyPI account + API token
   ```
   (Check the name is free on PyPI first; rename in `pyproject.toml` if not.)
3. **Ship it in Docker** if you don't want teammates installing Python:
   ```json
   { "mcpServers": { "coderag": { "command": "docker",
       "args": ["compose", "-f", "/abs/path/coderag/docker-compose.yml",
                "exec", "-T", "api", "coderag-mcp"] } } }
   ```
4. **Team-wide by default:** commit `.mcp.json` (Option B) — everyone who opens the repo in
   Claude Code gets the server automatically once they've done Steps 1–4.

The **VS Code extension** (`vscode-extension/`, prebuilt `.vsix` on the
[Releases page](https://github.com/galacticsurfer/coderag/releases)) is a *separate* thing: it
gives you Index/Search/Ask/Dashboard commands in the editor UI. The MCP server is what makes
**Claude Code** itself cheaper. You can use either or both.

## Step 8 — Measure it on *your* repo before believing any claim

```bash
coderag context "why does <something in your codebase> happen?" --repo myrepo --measure
```

prints the counterfactual: tokens to read the whole files containing that code vs the budgeted
context CodeRAG returns. The same numbers are persisted per query and shown on the dashboard
("Saved vs reading files", "Reduction vs files"), including for MCP calls made by Claude Code.

**On the bundled demo repo this measures only ~8%** — 11 tiny files, so retrieval reaches most of
the repo and fixed prompt scaffolding dominates. The win scales with *file size ÷ symbol size*:
on a real backend where you need one method out of a 900-line service, it is far larger. Run the
command above on your repository to get your real number — do not take ours.

## Honest expectations

- Savings scale with **repository size** — on a small repo the difference is minor; on a large
  backend, retrieving ~2k budgeted tokens instead of reading 20k+ of files is the win.
- The model still decides when to call the tools; the `CLAUDE.md` instruction matters.
- Default `hashing` embedder is offline/lexical. For genuinely semantic matching install the
  `embeddings` extra and set `CODERAG_EMBEDDING_PROVIDER=sentence_transformer` (pulls torch).
- Re-index after large changes or retrieval will reference stale code.
