import * as vscode from "vscode";

export interface Candidate {
  symbol_id: number;
  qualified_name: string;
  symbol_type: string;
  file_path: string;
  start_line: number;
  end_line: number;
  score: number;
  reasons: string[];
}

function config() {
  const c = vscode.workspace.getConfiguration("coderag");
  const folder = vscode.workspace.workspaceFolders?.[0];
  return {
    url: (c.get<string>("serverUrl") || "http://localhost:8000").replace(/\/+$/, ""),
    repository: c.get<string>("repository") || folder?.name || undefined,
    token: c.get<string>("apiToken") || "",
    workspacePath: folder?.uri.fsPath,
  };
}

async function request(method: string, path: string, body?: unknown): Promise<any> {
  const { url, token } = config();
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (token) headers["x-user"] = token;
  let res: Response;
  try {
    res = await fetch(`${url}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e: any) {
    throw new Error(
      `Cannot reach CodeRAG at ${url}. Is the server running ` +
        `(uvicorn coderag.api.app:app)?  [${e?.message ?? e}]`
    );
  }
  const text = await res.text();
  if (!res.ok) {
    let detail = text;
    try { detail = JSON.parse(text).detail ?? text; } catch { /* keep raw */ }
    throw new Error(`CodeRAG ${path} → ${res.status}: ${detail}`);
  }
  return text ? JSON.parse(text) : {};
}

export const repositoryName = () => config().repository;
export const workspacePath = () => config().workspacePath;
export const dashboardUrl = () => `${config().url}/dashboard`;

export const registerRepo = (name: string, path: string) =>
  request("POST", "/repositories", { name, path });
export const indexRepo = (id: number) =>
  request("POST", `/repositories/${id}/index`);
export const search = (query: string, limit = 25) =>
  request("POST", "/search", { query, repository: config().repository, limit });
export const context = (query: string) =>
  request("POST", "/context", { query, repository: config().repository });
export const ask = (query: string) =>
  request("POST", "/ask", { query, repository: config().repository });
