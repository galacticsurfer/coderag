"""CodeRAG command-line interface.

    coderag index ./path/to/repo
    coderag search "where are failed payments retried?"

Retrieval commands need only the database; `ask` (later) needs an LLM provider.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from coderag.core.config import get_settings
from coderag.core.logging import configure_logging
from coderag.db.base import session_scope

app = typer.Typer(add_completion=False, help="CodeRAG: token-efficient code RAG.")
console = Console()


@app.callback()
def _main(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    configure_logging("DEBUG" if verbose else "WARNING")


@app.command()
def index(
    path: str = typer.Argument(..., help="Path to the repository to index."),
    name: str | None = typer.Option(None, "--name", help="Repository name (default: dir name)."),
) -> None:
    """Full-index a repository into PostgreSQL."""
    with session_scope() as session:
        from coderag.service import run_index

        repo, stats = run_index(session, path, name)
        console.print(
            f"[green]Indexed[/] [bold]{repo.name}[/] @ {stats.commit_sha or 'no-commit'}: "
            f"{stats.files_indexed} files, {stats.symbols_indexed} symbols, "
            f"{stats.files_skipped} skipped, {stats.secrets_redacted} redactions "
            f"in {stats.duration_seconds:.2f}s"
        )


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query."),
    repo: str | None = typer.Option(None, "--repo", help="Repository name."),
    limit: int = typer.Option(10, "--limit", "-n"),
    semantic: bool = typer.Option(False, "--semantic/--no-semantic",
                                  help="Include semantic (vector) retrieval (Phase 3)."),
    graph: bool = typer.Option(False, "--graph/--no-graph",
                               help="Include one-hop dependency expansion (Phase 4)."),
) -> None:
    """Retrieve ranked symbols (no LLM required)."""
    settings = get_settings()
    with session_scope() as session:
        from coderag.service import run_search

        # Semantic requires embeddings to exist; degrade gracefully if unavailable.
        repo_obj, outcome = run_search(
            session, query, repo, top_n=limit, settings=settings,
            semantic=semantic, graph=graph,
        )
        _print_candidates(repo_obj.name, outcome)


def _print_candidates(repo_name: str, outcome) -> None:
    table = Table(title=f"Results in '{repo_name}'  ({outcome.latency_ms:.1f} ms)")
    table.add_column("#", justify="right", style="dim")
    table.add_column("score", justify="right")
    table.add_column("symbol", style="cyan", overflow="fold")
    table.add_column("type")
    table.add_column("location", style="dim", overflow="fold")
    table.add_column("why", style="magenta")
    for i, c in enumerate(outcome.candidates, 1):
        table.add_row(
            str(i), f"{c.fused_score:.4f}", c.qualified_name, c.symbol_type,
            f"{c.file_path}:{c.start_line}-{c.end_line}", c.explain(),
        )
    console.print(table)
    if not outcome.candidates:
        console.print("[yellow]No results.[/]")


if __name__ == "__main__":
    app()
