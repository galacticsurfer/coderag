import * as vscode from "vscode";
import * as api from "./api";

export function activate(ctx: vscode.ExtensionContext) {
  ctx.subscriptions.push(
    vscode.commands.registerCommand("coderag.index", indexWorkspace),
    vscode.commands.registerCommand("coderag.search", searchSymbols),
    vscode.commands.registerCommand("coderag.context", showContext),
    vscode.commands.registerCommand("coderag.ask", askQuestion),
    vscode.commands.registerCommand("coderag.openDashboard", () =>
      vscode.env.openExternal(vscode.Uri.parse(api.dashboardUrl()))
    )
  );
}

export function deactivate() {}

function fail(e: unknown) {
  vscode.window.showErrorMessage(e instanceof Error ? e.message : String(e));
}

/** Indexing step: register the workspace as a repository and full-index it. */
async function indexWorkspace() {
  const path = api.workspacePath();
  const name = api.repositoryName();
  if (!path || !name) {
    return fail(new Error("Open a workspace folder first."));
  }
  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: `CodeRAG: indexing ${name}…` },
    async () => {
      try {
        const repo = await api.registerRepo(name, path);
        const s = await api.indexRepo(repo.id);
        vscode.window.showInformationMessage(
          `CodeRAG indexed ${name}: ${s.symbols_indexed} symbols, ` +
            `${s.embeddings_created} embeddings, ${s.relationships} relationships ` +
            `in ${s.duration_seconds?.toFixed?.(2) ?? "?"}s.`
        );
      } catch (e) {
        fail(e);
      }
    }
  );
}

async function searchSymbols() {
  const query = await vscode.window.showInputBox({ prompt: "CodeRAG: search symbols" });
  if (!query) return;
  try {
    const r = await api.search(query);
    if (!r.candidates?.length) {
      vscode.window.showInformationMessage("CodeRAG: no results.");
      return;
    }
    const pick = await vscode.window.showQuickPick(
      r.candidates.map((c: api.Candidate) => ({
        label: c.qualified_name,
        description: `${c.symbol_type} · ${c.score.toFixed(3)} · ${c.reasons.join(",")}`,
        detail: `${c.file_path}:${c.start_line}-${c.end_line}`,
        c,
      })),
      { title: `CodeRAG · ${r.repository} · ${r.latency_ms.toFixed(0)}ms`, matchOnDetail: true }
    );
    if (pick) await openAt((pick as any).c.file_path, (pick as any).c.start_line);
  } catch (e) {
    fail(e);
  }
}

async function showContext() {
  const seed = selectedText();
  const query = await vscode.window.showInputBox({
    prompt: "CodeRAG: build context for…", value: seed,
  });
  if (!query) return;
  try {
    const r = await api.context(query);
    const a = r.accounting;
    const rows = (r.entries || [])
      .map((e: any) => `  [${e.category}] ${e.qualified_name}  (${e.tokens} tok)`)
      .join("\n");
    const summary =
      `selected ${a.candidates_selected}/${a.candidates_found} · ` +
      `context ${a.context_tokens} of ${a.candidate_tokens} candidate tokens ` +
      `(−${a.token_reduction_from_candidates}%) · prompt ${a.final_prompt_tokens} tokens`;
    showWebview("CodeRAG context", `${summary}\n\nSELECTED SYMBOLS:\n${rows}\n\n` +
      `--- PROMPT ---\n${r.prompt ?? ""}`);
  } catch (e) {
    fail(e);
  }
}

async function askQuestion() {
  const seed = selectedText();
  const query = await vscode.window.showInputBox({ prompt: "Ask CodeRAG", value: seed });
  if (!query) return;
  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "CodeRAG: asking…" },
    async () => {
      try {
        const r = await api.ask(query);
        const u = r.usage, a = r.accounting;
        showWebview("CodeRAG answer",
          `${r.answer}\n\n— ${u.model} · input ${u.input_tokens} / output ${u.output_tokens} ` +
          `tokens · context ${a.context_tokens} (−${a.token_reduction_from_candidates}% vs candidates)`);
      } catch (e) {
        fail(e);
      }
    }
  );
}

function selectedText(): string {
  const ed = vscode.window.activeTextEditor;
  return ed ? ed.document.getText(ed.selection) : "";
}

async function openAt(path: string, line: number) {
  const root = vscode.workspace.workspaceFolders?.[0]?.uri;
  if (!root) return;
  const doc = await vscode.workspace.openTextDocument(vscode.Uri.joinPath(root, path));
  const ed = await vscode.window.showTextDocument(doc);
  const pos = new vscode.Position(Math.max(0, line - 1), 0);
  ed.selection = new vscode.Selection(pos, pos);
  ed.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter);
}

function showWebview(title: string, body: string) {
  const panel = vscode.window.createWebviewPanel(
    "coderag", title, vscode.ViewColumn.Beside, {}
  );
  const esc = (s: string) =>
    s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c] as string));
  panel.webview.html =
    `<body style="font-family:var(--vscode-editor-font-family);padding:12px">` +
    `<pre style="white-space:pre-wrap">${esc(body)}</pre></body>`;
}
