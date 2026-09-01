"""MCP server — expose CodeRAG retrieval to Claude Code (and any MCP client).

Why this exists: Claude Code gathers context by globbing/grepping and reading
whole files, which is token-expensive on a large repo. With this server
registered, it can instead call ``coderag_context`` / ``coderag_search`` and
receive only the budgeted, deduplicated set of symbols relevant to the task —
that is where the input-token savings come from.

Runs over stdio and talks to PostgreSQL directly (same code path as the CLI), so
no HTTP server is required — only ``CODERAG_DATABASE_URL``.

Register with Claude Code:

    claude mcp add coderag -- coderag-mcp

...or add to ``.mcp.json`` — see docs/claude-code.md.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from coderag.core.config import get_settings
from coderag.db.base import session_scope

server = MCPServer(
    name="coderag",
    instructions=(
        "Token-efficient code retrieval over indexed repositories. "
        "Prefer coderag_context for 'why/how does X work' questions and "
        "coderag_search to locate symbols, INSTEAD of reading whole files — "
        "these return only the relevant code and cost far fewer tokens."
    ),
)


def _repo_arg(repository: str | None) -> str | None:
    return repository or None


@server.tool(
    description=(
        "Search an indexed repository for the symbols most relevant to a query. "
        "Hybrid retrieval (exact symbol + full-text + semantic + code graph). "
        "Returns ranked symbols with file, line range, score and why each matched. "
        "Use this instead of grepping/reading files to find code."
    )
)
def coderag_search(
    query: str, repository: str | None = None, limit: int = 10
) -> list[dict[str, Any]]:
    from coderag.service import run_search

    with session_scope() as session:
        _repo, outcome = run_search(
            session, query, _repo_arg(repository), top_n=limit,
            semantic=True, graph=True, record=True,
        )
        return [
            {
                "symbol": c.qualified_name,
                "type": c.symbol_type,
                "file": c.file_path,
                "lines": f"{c.start_line}-{c.end_line}",
                "score": round(c.fused_score, 5),
                "why": sorted(c.reasons),
                "tokens": c.token_count,
            }
            for c in outcome.candidates
        ]


@server.tool(
    description=(
        "Build a token-budgeted context package for a question about the code and "
        "return the actual code text (target symbol, dependencies, callers, tests). "
        "This is the token-efficient replacement for reading several whole files."
    )
)
def coderag_context(
    query: str, repository: str | None = None, max_tokens: int | None = None
) -> dict[str, Any]:
    from coderag.service import run_context

    with session_scope() as session:
        _repo, package, _outcome = run_context(
            session, query, _repo_arg(repository), max_tokens=max_tokens,
        )
        a = package.accounting
        return {
            "context": package.prompt_text,
            "selected_symbols": [
                {
                    "category": e.category,
                    "symbol": e.candidate.qualified_name,
                    "file": e.candidate.file_path,
                    "lines": f"{e.candidate.start_line}-{e.candidate.end_line}",
                    "tokens": e.tokens,
                }
                for e in package.entries
            ],
            "token_accounting": a.as_dict(),
        }


@server.tool(
    description=(
        "Fetch the full source of one symbol by qualified name (e.g. "
        "'payments.payment_service.PaymentService.retry_payment')."
    )
)
def coderag_symbol(qualified_name: str, repository: str | None = None) -> dict[str, Any]:
    from sqlalchemy import select

    from coderag.db.models import Symbol
    from coderag.service import resolve_repository

    with session_scope() as session:
        repo = resolve_repository(session, _repo_arg(repository))
        sym = session.scalar(
            select(Symbol).where(
                Symbol.repository_id == repo.id,
                Symbol.qualified_name == qualified_name,
            )
        )
        if sym is None:
            sym = session.scalar(
                select(Symbol).where(
                    Symbol.repository_id == repo.id,
                    Symbol.qualified_name.ilike(f"%.{qualified_name}"),
                )
            )
        if sym is None:
            return {"error": f"symbol {qualified_name!r} not found in {repo.name}"}
        return {
            "symbol": sym.qualified_name,
            "type": sym.symbol_type,
            "file": sym.file_path,
            "lines": f"{sym.start_line}-{sym.end_line}",
            "signature": sym.signature,
            "docstring": sym.docstring,
            "tokens": sym.token_count,
            "source": sym.source_code,
        }


@server.tool(
    description=(
        "List which repositories are indexed and available to query, with their "
        "indexed commit."
    )
)
def coderag_repositories() -> list[dict[str, Any]]:
    from sqlalchemy import select

    from coderag.db.models import Repository

    with session_scope() as session:
        return [
            {
                "name": r.name,
                "path": r.local_path,
                "indexed_commit": r.indexed_commit_sha,
                "branch": r.default_branch,
            }
            for r in session.scalars(select(Repository).order_by(Repository.id))
        ]


@server.tool(
    description=(
        "Index (or re-index) a repository so it can be queried. Use incremental=true "
        "to update only what changed since the last indexed commit."
    )
)
def coderag_index(
    path: str, name: str | None = None, incremental: bool = False
) -> dict[str, Any]:
    from coderag.service import run_index

    with session_scope() as session:
        repo, stats = run_index(session, path, name, incremental=incremental)
        return {
            "repository": repo.name,
            "files_indexed": stats.files_indexed,
            "symbols_indexed": stats.symbols_indexed,
            "embeddings_created": stats.embeddings_created,
            "relationships": stats.relationships_created,
            "commit": stats.commit_sha,
            "seconds": round(stats.duration_seconds, 2),
        }


def main() -> None:
    get_settings()  # fail fast on bad configuration
    server.run("stdio")


if __name__ == "__main__":
    main()
