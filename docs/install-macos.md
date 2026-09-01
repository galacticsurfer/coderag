# Install on macOS

Two paths. **Path A needs no Docker, no Homebrew Postgres, and no `sudo`** — pick it unless you
already run Docker Desktop.

Requirements either way: **Python 3.12+** and **git**.

---

## Path A — no Docker (recommended)

CodeRAG ships a `localdb` command that runs a real PostgreSQL **with pgvector**, bundled and
rootless, as your own user.

### 1. Install pipx (once)

```bash
brew install pipx && pipx ensurepath
# no Homebrew? python3 -m pip install --user pipx && python3 -m pipx ensurepath
```

Open a new terminal afterwards so your `PATH` updates.

### 2. Install CodeRAG

```bash
pipx install "coderag-ai[mcp,localdb]"
```

Check:

```bash
coderag --help && which coderag-mcp
```

### 3. Start the database

```bash
coderag localdb start
```

It prints the URL to export — copy that line:

```bash
export CODERAG_DATABASE_URL='postgresql+psycopg://postgres:@/postgres?host=/…'
```

Put it in `~/.zshrc` so every shell (and Claude Code) sees it. The server keeps running in the
background; `coderag localdb status` / `stop` control it.

> If migrations didn't apply automatically, clone the repo and run `alembic upgrade head`
> with `CODERAG_DATABASE_URL` set.

### 4. Index your project

```bash
coderag index /path/to/your-project --name myproject
coderag search "where is authentication handled?" --repo myproject
```

### 5. (Optional) Use it from Claude Code — this is what saves tokens

```bash
claude mcp add coderag \
  --env CODERAG_DATABASE_URL="$CODERAG_DATABASE_URL" \
  --env CODERAG_DEFAULT_REPOSITORY=myproject \
  -- coderag-mcp
```

Then add the `CLAUDE.md` nudge from [`claude-code.md`](claude-code.md#step-6) — without it
Claude Code will keep reading whole files and you'll save nothing.

### 6. (Optional) Dashboard

The dashboard is served by the API, so run it from a clone:

```bash
git clone https://github.com/galacticsurfer/coderag && cd coderag
pip install -e . && uvicorn coderag.api.app:app --port 8000
# open http://localhost:8000/dashboard
```

---

## Path B — Docker Desktop

If you already have Docker Desktop running:

```bash
git clone https://github.com/galacticsurfer/coderag && cd coderag
make up          # Postgres + API + migrations + demo index
# http://localhost:8000/dashboard
```

Then index your own code:

```bash
CODERAG_REPO_PATH=/path/to/your-project make up
docker compose exec api coderag index /workspace --name myproject
```

---

## Apple Silicon / Intel

Both work. `pgserver` publishes macOS wheels for arm64 and x86_64; the optional
`embeddings` extra (PyTorch) also supports both, but it's a large download — skip it unless you
want semantic (rather than lexical) matching.

## Uninstall

```bash
coderag localdb stop
pipx uninstall coderag-ai
rm -rf ~/.coderag        # database files
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `command not found: coderag` | `pipx ensurepath`, then open a new terminal. |
| `no repositories indexed yet` | Run `coderag index /path/to/project --name myproject`. |
| `multiple repositories; pass --repo` | Add `--repo myproject` or set `CODERAG_DEFAULT_REPOSITORY`. |
| Claude Code doesn't see the tools | Run `/mcp` in Claude Code; confirm `CODERAG_DATABASE_URL` is in the `claude mcp add` env. |
| `Cannot run ask: … API key` | Expected — only CodeRAG's own `ask` needs an LLM. Search/context/MCP don't. |
