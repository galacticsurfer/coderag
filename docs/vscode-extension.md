# Building a VS Code extension for CodeRAG

**Short answer: yes.** CodeRAG already exposes an HTTP API (`coderag.api.app`), so a VS Code
extension is a thin TypeScript client over `/search`, `/context`, and `/ask` — no changes to
the core are required. The MVP deliberately did *not* ship an IDE plugin (ADR/§33), but the
API was designed so one drops in cleanly. This document is the step-by-step recipe.

> Design principle: the extension talks **only** to a CodeRAG server the user runs (locally or
> self-hosted). Source code and prompts never go anywhere the CLI wouldn't already send them.

## Architecture

```
VS Code (extension host, TypeScript)
   │  HTTP (localhost:8000 by default)
   ▼
CodeRAG FastAPI  ──►  retrieval / context / ask  ──►  PostgreSQL + (optional) Claude
```

The extension is a **client**. All intelligence stays server-side, which keeps the extension
tiny and lets it inherit every improvement to retrieval/ranking without a redeploy.

## Prerequisites

- Node.js 18+ and `npm`.
- A running CodeRAG API: `uvicorn coderag.api.app:app --port 8000` (or `docker compose up`).
- The repo indexed once: `coderag index /path/to/repo --name myrepo`.
- Scaffolding tools: `npm i -g yo generator-code @vscode/vsce`.

## Step 1 — Scaffold

```bash
yo code            # choose: New Extension (TypeScript), name "coderag-vscode"
cd coderag-vscode
```

This produces `package.json` (the extension manifest), `src/extension.ts`, and a build setup.

## Step 2 — Declare commands & settings

In `package.json`:

```jsonc
{
  "contributes": {
    "commands": [
      { "command": "coderag.search",  "title": "CodeRAG: Search symbols" },
      { "command": "coderag.context", "title": "CodeRAG: Show context for selection" },
      { "command": "coderag.ask",     "title": "CodeRAG: Ask about this code" }
    ],
    "configuration": {
      "title": "CodeRAG",
      "properties": {
        "coderag.serverUrl": { "type": "string", "default": "http://localhost:8000" },
        "coderag.repository": { "type": "string", "default": "" },
        "coderag.apiToken":  { "type": "string", "default": "",
          "description": "Optional; sent as the x-user header for authorization." }
      }
    }
  },
  "activationEvents": ["onCommand:coderag.search", "onCommand:coderag.context",
                       "onCommand:coderag.ask"]
}
```

## Step 3 — Call the API

`src/api.ts` — a minimal client (uses the built-in `fetch` in modern Node/VS Code):

```ts
import * as vscode from "vscode";

function cfg() {
  const c = vscode.workspace.getConfiguration("coderag");
  return { url: c.get<string>("serverUrl")!, repo: c.get<string>("repository") || undefined,
           token: c.get<string>("apiToken") || "" };
}

async function post(path: string, body: unknown) {
  const { url, token } = cfg();
  const res = await fetch(`${url}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json", ...(token ? { "x-user": token } : {}) },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`CodeRAG ${path} → ${res.status}: ${await res.text()}`);
  return res.json();
}

export const search  = (query: string) => post("/search",  { query, repository: cfg().repo });
export const context = (query: string) => post("/context", { query, repository: cfg().repo });
export const ask     = (query: string) => post("/ask",     { query, repository: cfg().repo });
```

## Step 4 — Wire the commands

`src/extension.ts`:

```ts
import * as vscode from "vscode";
import { search, context, ask } from "./api";

export function activate(ctx: vscode.ExtensionContext) {
  // Search → QuickPick of ranked symbols → open the file at the symbol's line
  ctx.subscriptions.push(vscode.commands.registerCommand("coderag.search", async () => {
    const q = await vscode.window.showInputBox({ prompt: "CodeRAG search" });
    if (!q) return;
    const r: any = await search(q);
    const pick = await vscode.window.showQuickPick(
      r.candidates.map((c: any) => ({
        label: c.qualified_name,
        description: `${c.symbol_type} · score ${c.score.toFixed(3)} · ${c.reasons.join(",")}`,
        detail: `${c.file_path}:${c.start_line}-${c.end_line}`,
        c,
      })),
      { title: `CodeRAG · ${r.repository} · ${r.latency_ms.toFixed(0)}ms`, matchOnDetail: true }
    );
    if (pick) await openAt((pick as any).c.file_path, (pick as any).c.start_line);
  }));

  // Ask → run the selection/word through retrieval + LLM, render in a webview
  ctx.subscriptions.push(vscode.commands.registerCommand("coderag.ask", async () => {
    const ed = vscode.window.activeTextEditor;
    const seed = ed?.document.getText(ed.selection) || "";
    const q = await vscode.window.showInputBox({ prompt: "Ask CodeRAG", value: seed });
    if (!q) return;
    await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: "CodeRAG asking…" },
      async () => {
        const r: any = await ask(q);
        const panel = vscode.window.createWebviewPanel("coderag", "CodeRAG answer",
          vscode.ViewColumn.Beside, {});
        panel.webview.html =
          `<pre style="white-space:pre-wrap;font-family:var(--vscode-editor-font-family)">` +
          escapeHtml(r.answer) +
          `\n\n— input ${r.usage.input_tokens} / output ${r.usage.output_tokens} tokens ` +
          `(context ${r.accounting.context_tokens}, −${r.accounting.token_reduction_from_candidates}%)</pre>`;
      }
    );
  }));
}

async function openAt(path: string, line: number) {
  const root = vscode.workspace.workspaceFolders?.[0].uri;
  if (!root) return;
  const doc = await vscode.workspace.openTextDocument(vscode.Uri.joinPath(root, path));
  const ed = await vscode.window.showTextDocument(doc);
  const pos = new vscode.Position(Math.max(0, line - 1), 0);
  ed.selection = new vscode.Selection(pos, pos);
  ed.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter);
}

const escapeHtml = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
```

The `coderag.context` command is the same shape as `ask` but calls `context(q)` and renders
`prompt` + the `entries`/`accounting` table — handy for developers debugging token usage.

## Step 5 — Run & debug

Press **F5** in VS Code to launch an Extension Development Host, then run the commands from the
Command Palette. Point `coderag.serverUrl` at your running API and set `coderag.repository`.

## Step 6 — Package & publish

```bash
vsce package                      # → coderag-vscode-0.0.1.vsix (share/install directly)
code --install-extension coderag-vscode-0.0.1.vsix
# to publish on the Marketplace you need a publisher + PAT:
vsce login <publisher> && vsce publish
```

## Nice-to-haves (roadmap)

- **CodeLens / hover**: "Ask CodeRAG about `PaymentService.retry_payment`" above symbols
  (use the `/symbols/{id}` and `/symbols/{id}/relationships` endpoints).
- **Tree view** of a symbol's callers/callees/tests from the relationships endpoint.
- **Inline diff** apply for the analyzer `--fix` proposals (once patch apply/verify lands).
- **Streaming answers**: add a streaming `/ask` (the provider already implements SSE) and read
  it incrementally in the webview.
- **Auto-index on save**: call `POST /repositories/{id}/index` (incremental) via a file watcher.

## Effort & risk

A useful MVP extension (search + ask + context, as above) is roughly **1–2 days**. It carries
no core-code risk because it is purely an API client — all retrieval logic, token budgeting,
and security controls remain server-side and already tested.
