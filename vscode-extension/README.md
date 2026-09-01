# CodeRAG for VS Code

Token-efficient, structure-aware code search / context / Q&A over a
[CodeRAG](https://github.com/galacticsurfer/coderag) server, right inside the editor.

This extension is a **thin client** — all retrieval, ranking, token budgeting, and security
stay in the CodeRAG server, so the extension is tiny and always up to date.

## Prerequisites (the server + the indexing step)

1. **Run the CodeRAG server** (needs PostgreSQL+pgvector — `docker compose up -d` in the repo):
   ```bash
   pip install "coderag @ git+https://github.com/galacticsurfer/coderag"
   export CODERAG_DATABASE_URL=postgresql+psycopg://coderag:coderag@localhost:5432/coderag
   alembic upgrade head          # once
   uvicorn coderag.api.app:app --port 8000
   ```
2. **Index your repo — yes, there is an indexing step.** Either from the CLI
   (`coderag index /path/to/repo`) **or one click in the editor**: run
   **“CodeRAG: Index this workspace”** from the Command Palette. Re-run after big changes
   (or `coderag index . --incremental`).

## Install

- **From a packaged file:** grab/build `coderag-vscode-0.1.0.vsix` (see below) then
  `code --install-extension coderag-vscode-0.1.0.vsix` — or in VS Code:
  *Extensions ▸ ⋯ ▸ Install from VSIX…*
- **Build the VSIX yourself:**
  ```bash
  cd vscode-extension
  npm install
  npm run package        # → coderag-vscode-0.1.0.vsix
  ```
- **Hack on it:** open this folder in VS Code and press **F5** (Extension Development Host).

## Commands (Command Palette)

| Command | Does |
|---------|------|
| CodeRAG: Index this workspace | Register + full-index the open folder (the indexing step). |
| CodeRAG: Search symbols | Ranked symbols → pick one → jump to the file/line. |
| CodeRAG: Show context for a question | The exact budgeted context + token accounting. |
| CodeRAG: Ask about this code | Retrieve context and answer via the configured LLM. |
| CodeRAG: Open token dashboard | Opens the server's `/dashboard` in your browser. |

Right-click in the editor → **CodeRAG: Ask about this code** uses your selection as the seed.

## Settings

| Setting | Default | Notes |
|---------|---------|-------|
| `coderag.serverUrl` | `http://localhost:8000` | Where the CodeRAG API runs. |
| `coderag.repository` | *(workspace folder name)* | Which indexed repo to query. |
| `coderag.apiToken` | `""` | Optional; sent as `x-user` for authorization. |

`Search` and `Show context` need no LLM; only `Ask` does (configure `CODERAG_ANTHROPIC_API_KEY`
on the server). Licensed Apache-2.0.
