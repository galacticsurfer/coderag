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


localdb_app = typer.Typer(help="Rootless local PostgreSQL (no Docker/sudo needed).")
app.add_typer(localdb_app, name="localdb")


@localdb_app.command("start")
def localdb_start(
    pgdata: str | None = typer.Option(None, "--pgdata", help="Data directory."),
    migrate: bool = typer.Option(True, "--migrate/--no-migrate",
                                 help="Apply database migrations after starting."),
) -> None:
    """Start a local PostgreSQL (with pgvector) and print its URL."""
    from coderag import localdb

    try:
        url = localdb.start(pgdata)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from None
    console.print(f"[green]PostgreSQL running.[/]\n\n  export CODERAG_DATABASE_URL='{url}'\n")
    if migrate:
        # Uses Alembic's Python API against the packaged migrations — an installed
        # wheel has neither the `alembic` console script on PATH nor alembic.ini.
        from coderag.db.migrate import upgrade_to_head

        try:
            upgrade_to_head(url)
            console.print("[green]Migrations applied.[/]")
        except Exception as exc:
            console.print(f"[red]Migrations failed:[/] {exc}")
            console.print("[yellow]Retry with:[/] coderag migrate")
            raise typer.Exit(1) from None


@localdb_app.command("stop")
def localdb_stop(
    pgdata: str | None = typer.Option(None, "--pgdata"),
) -> None:
    """Stop the local PostgreSQL server."""
    from coderag import localdb

    localdb.stop(pgdata)
    console.print("[green]Stopped.[/]")


@localdb_app.command("status")
def localdb_status(
    pgdata: str | None = typer.Option(None, "--pgdata"),
) -> None:
    """Show whether the local PostgreSQL server is running."""
    from coderag import localdb

    pid = localdb.status(pgdata)
    console.print(f"[green]running (pid {pid})[/]" if pid else "[yellow]not running[/]")


@app.command()
def setup(
    path: str = typer.Argument(".", help="Repository to index (default: current dir)."),
    name: str | None = typer.Option(None, "--name", help="Repository name."),
    mcp: bool = typer.Option(True, "--mcp/--no-mcp", help="Register with Claude Code."),
    skill: bool = typer.Option(True, "--skill/--no-skill",
                               help="Install the /token-lean skill for Claude Code."),
    claude_md: bool = typer.Option(True, "--claude-md/--no-claude-md",
                                   help="Append the retrieval nudge to CLAUDE.md."),
) -> None:
    """One command: start the database, migrate, index, and wire up Claude Code."""
    from pathlib import Path

    from coderag import localdb
    from coderag.core.config import get_settings
    from coderag.db.migrate import upgrade_to_head
    from coderag.setup_flow import (
        SetupResult,
        append_nudge,
        claude_md_needs_nudge,
        install_skill,
        register_mcp,
    )

    repo_path = Path(path).expanduser().resolve()
    repo_name = name or repo_path.name
    res = SetupResult(repository=repo_name)

    # 1. database — reuse an already-configured one, else start the local server
    import os

    if os.environ.get("CODERAG_DATABASE_URL"):
        res.database_url = get_settings().database_url
        res.skip("using CODERAG_DATABASE_URL from the environment")
    else:
        try:
            res.database_url = localdb.start()
            res.ok("local PostgreSQL running (URL recorded; no export needed)")
        except RuntimeError as exc:
            res.warn(str(exc).splitlines()[0])
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(1) from None

    # 2. schema
    try:
        upgrade_to_head(res.database_url)
        res.ok("database schema up to date")
    except Exception as exc:
        res.warn(f"migrations failed: {str(exc)[:120]}")
        console.print(f"[red]Migrations failed:[/] {exc}")
        raise typer.Exit(1) from None

    # 3. index
    get_settings.cache_clear()
    os.environ["CODERAG_DATABASE_URL"] = res.database_url or ""
    with session_scope() as session:
        from coderag.service import run_index

        repo, stats = run_index(session, str(repo_path), repo_name)
        res.symbols = stats.symbols_indexed
    res.ok(f"indexed {repo_name}: {stats.files_indexed} files, "
           f"{stats.symbols_indexed} symbols, {stats.relationships_created} relationships")

    # 4. Claude Code MCP registration
    if mcp:
        done, msg = register_mcp("coderag", res.database_url or "", repo_name)
        (res.ok if done else res.warn)(msg)
    else:
        res.skip("MCP registration skipped (--no-mcp)")

    # 5. the token-lean skill (retrieval preference + output discipline)
    if skill:
        done, msg = install_skill("user")
        (res.ok if done else res.warn)(msg)
    else:
        res.skip("skill install skipped (--no-skill)")

    # 6. the nudge — without it Claude Code never calls the tools
    if claude_md:
        target = repo_path / "CLAUDE.md"
        if claude_md_needs_nudge(target):
            append_nudge(target)
            res.ok(f"added the retrieval nudge to {target}")
        else:
            res.skip("CLAUDE.md already contains the nudge")
    else:
        res.skip("CLAUDE.md untouched (--no-claude-md)")

    icons = {"ok": "[green]✓[/]", "skip": "[dim]·[/]", "warn": "[yellow]![/]"}
    console.print("\n[bold]CodeRAG setup[/]")
    for status, msg in res.steps:
        console.print(f"  {icons[status]} {msg}")
    console.print(
        "\nTry it:  [bold]coderag search \"where is X handled?\"[/]\n"
        "In Claude Code: run [bold]/mcp[/] (expect 'coderag' connected), then ask a "
        "question normally — MCP tools are not slash commands.\n"
        "Dashboard: [bold]uvicorn coderag.api.app:app --port 8000[/] "
        "-> http://localhost:8000/dashboard"
    )


@app.command()
def proxy(
    port: int = typer.Option(8788, "--port"),
    host: str = typer.Option("127.0.0.1", "--host",
                             help="Bind address. Keep it loopback-only."),
    upstream: str = typer.Option(
        "https://api.anthropic.com", "--upstream",
        help="Where to forward traffic (another proxy works — chaining is fine)."),
) -> None:
    """Run the observability proxy: forwards LLM traffic unmodified, records real
    provider-billed token usage to the dashboard.

    Point your agent at it:  export ANTHROPIC_BASE_URL=http://127.0.0.1:8788
    """
    import uvicorn

    from coderag.proxy import create_app

    console.print(
        f"[green]Observability proxy[/] -> forwarding to [cyan]{upstream}[/]\n\n"
        f"  export ANTHROPIC_BASE_URL=http://{host}:{port}\n\n"
        "[dim]Traffic passes through byte-for-byte unmodified. Only token counts, "
        "model, latency, and status are recorded — never prompts, responses, or "
        "credentials. View at the dashboard's 'Est. $ LLM spend' / LLM tiles.[/]"
    )
    uvicorn.run(create_app(upstream), host=host, port=port, log_level="warning")


@app.command()
def migrate(
    database_url: str | None = typer.Option(
        None, "--database-url", help="Defaults to CODERAG_DATABASE_URL."
    ),
) -> None:
    """Create/upgrade the database schema (works from a pip/pipx install)."""
    from coderag.db.migrate import current_revision, upgrade_to_head

    try:
        url = upgrade_to_head(database_url)
    except Exception as exc:
        console.print(f"[red]Migration failed:[/] {exc}")
        raise typer.Exit(1) from None
    console.print(f"[green]Database is up to date[/] (revision {current_revision(url)}).")


@app.command()
def index(
    path: str = typer.Argument(..., help="Path to the repository to index."),
    name: str | None = typer.Option(None, "--name", help="Repository name (default: dir name)."),
    incremental: bool = typer.Option(False, "--incremental",
                                     help="Only reindex what changed since the last commit."),
) -> None:
    """Index a repository into PostgreSQL (full, or --incremental)."""
    with session_scope() as session:
        from coderag.service import run_index

        repo, stats = run_index(session, path, name, incremental=incremental)
        console.print(
            f"[green]Indexed[/] [bold]{repo.name}[/] @ {stats.commit_sha or 'no-commit'}: "
            f"{stats.files_indexed} files, {stats.symbols_indexed} symbols, "
            f"{stats.embeddings_created} embeddings, "
            f"{stats.relationships_created} relationships, "
            f"{stats.files_skipped} skipped, {stats.secrets_redacted} redactions "
            f"in {stats.duration_seconds:.2f}s"
        )


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query."),
    repo: str | None = typer.Option(None, "--repo", help="Repository name."),
    limit: int = typer.Option(10, "--limit", "-n"),
    semantic: bool = typer.Option(True, "--semantic/--no-semantic",
                                  help="Include semantic (vector) retrieval."),
    graph: bool = typer.Option(True, "--graph/--no-graph",
                               help="Include one-hop dependency expansion."),
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


@app.command()
def context(
    query: str = typer.Argument(..., help="Task / question."),
    repo: str | None = typer.Option(None, "--repo"),
    max_tokens: int | None = typer.Option(None, "--max-tokens",
                                          help="Override the context token budget."),
    show_prompt: bool = typer.Option(False, "--show-prompt",
                                     help="Print the full prompt that would go to the LLM."),
    measure: bool = typer.Option(False, "--measure",
                                 help="Compare against reading the whole files instead."),
) -> None:
    """Show the exact context (and token accounting) that WOULD be sent to the LLM.

    No LLM is called — useful for debugging token usage.
    """
    with session_scope() as session:
        from coderag.service import run_context

        repo_obj, package, _outcome = run_context(session, query, repo, max_tokens=max_tokens)
        acct = package.accounting
        table = Table(title=f"Context for '{repo_obj.name}'")
        table.add_column("category")
        table.add_column("symbol", style="cyan", overflow="fold")
        table.add_column("lines", justify="right", style="dim")
        table.add_column("tokens", justify="right")
        table.add_column("why", style="magenta")
        for title, entries in package.sections():
            for e in entries:
                c = e.candidate
                table.add_row(title, c.qualified_name,
                              f"{c.start_line}-{c.end_line}", str(e.tokens), c.explain())
        console.print(table)
        console.print(
            f"[bold]selected[/] {acct.candidates_selected}/{acct.candidates_found}  "
            f"[bold]candidate_tokens[/] {acct.candidate_tokens}  "
            f"[bold]context_tokens[/] {acct.context_tokens}  "
            f"[bold]dropped[/] {acct.dropped_tokens}  "
            f"[bold]final_prompt_tokens[/] {acct.final_prompt_tokens}  "
            f"[green]reduction {acct.token_reduction_from_candidates}%[/]"
        )
        if measure:
            t = Table(title="Measured: reading whole files vs CodeRAG context")
            t.add_column("approach")
            t.add_column("input tokens", justify="right")
            t.add_row(
                f"read the {acct.baseline_files} whole file(s) containing this code",
                f"{acct.baseline_tokens:,}",
            )
            t.add_row("CodeRAG budgeted context", f"{acct.context_tokens:,}")
            t.add_row("CodeRAG full prompt (incl. scaffolding)",
                      f"{acct.final_prompt_tokens:,}")
            console.print(t)
            if acct.baseline_tokens:
                price = get_settings().price_input_per_mtok
                saved_usd = acct.tokens_saved_vs_files / 1e6 * price
                console.print(
                    f"[green]saved {acct.tokens_saved_vs_files:,} tokens "
                    f"({acct.reduction_vs_files}%) vs opening those files[/]  "
                    f"[dim]≈ ${saved_usd:.4f} at ${price:.2f}/M input tokens; "
                    f"× 1,000 similar queries ≈ ${saved_usd * 1000:.2f}[/]\n"
                    f"[dim]Estimate at configured prices, not billing data. On a "
                    f"flat-rate plan this is headroom, not a refund.[/]"
                )
            else:
                console.print("[yellow]No baseline recorded (nothing selected).[/]")
        if show_prompt:
            console.print("\n" + package.prompt_text)


@app.command()
def ask(
    query: str = typer.Argument(..., help="Question to answer with repo context + LLM."),
    repo: str | None = typer.Option(None, "--repo"),
    max_tokens: int | None = typer.Option(None, "--max-tokens", help="Context token budget."),
    show_tokens: bool = typer.Option(True, "--show-tokens/--no-show-tokens"),
) -> None:
    """Retrieve context and ask the configured LLM (needs an LLM provider)."""
    with session_scope() as session:
        from coderag.service import run_ask

        try:
            repo_obj, package, response, outcome = run_ask(
                session, query, repo, max_tokens=max_tokens
            )
        except RuntimeError as exc:
            console.print(f"[red]Cannot run ask:[/] {exc}")
            raise typer.Exit(2) from None
        console.print(f"\n[bold cyan]Answer[/] (repo: {repo_obj.name}):\n")
        console.print(response.text)
        if show_tokens:
            u, a = response.usage, package.accounting
            console.print(
                f"\n[dim]retrieval {outcome.latency_ms:.0f}ms · llm {u.latency_ms:.0f}ms · "
                f"model {u.model}[/]\n"
                f"[dim]input_tokens {u.input_tokens} · output_tokens {u.output_tokens} · "
                f"cached {u.cached_input_tokens} · "
                f"context_tokens {a.context_tokens} (from {a.candidate_tokens} candidate "
                f"tokens, -{a.token_reduction_from_candidates}%)[/]"
            )


@app.command()
def eval(
    dataset: str | None = typer.Option(None, "--dataset", help="Path to eval JSON."),
    repo: str | None = typer.Option(None, "--repo"),
    semantic: bool = typer.Option(True, "--semantic/--no-semantic"),
    graph: bool = typer.Option(True, "--graph/--no-graph"),
    rerank: bool = typer.Option(False, "--rerank", help="Apply the optional reranker."),
) -> None:
    """Evaluate retrieval quality: Recall@K, MRR, and token metrics."""
    from coderag.evaluation.datasets import DEMO_DATASET, load_dataset
    from coderag.evaluation.harness import evaluate_retrieval, persist_eval_run

    ds = dataset or DEMO_DATASET
    cases = load_dataset(ds)
    with session_scope() as session:
        from coderag.service import resolve_repository

        repo_obj = resolve_repository(session, repo)
        m = evaluate_retrieval(session, repo_obj, cases, semantic=semantic, graph=graph,
                               rerank=rerank)
        persist_eval_run(session, name=f"eval:{repo_obj.name}", dataset=ds, metrics=m)

    table = Table(title=f"Retrieval eval — {repo_obj.name} ({m.n_cases} cases)")
    table.add_column("metric")
    table.add_column("value", justify="right")
    for k in sorted(m.recall_at):
        table.add_row(f"Recall@{k}", f"{m.recall_at[k]:.3f}")
    table.add_row("MRR", f"{m.mrr:.3f}")
    table.add_row("avg retrieved tokens", f"{m.avg_candidate_tokens:.0f}")
    table.add_row("avg context tokens", f"{m.avg_context_tokens:.0f}")
    table.add_row("avg retrieval latency (ms)", f"{m.avg_retrieval_latency_ms:.2f}")
    console.print(table)


@app.command()
def benchmark(
    dataset: str | None = typer.Option(None, "--dataset"),
    repo: str | None = typer.Option(None, "--repo"),
    compare_baseline: bool = typer.Option(False, "--compare-baseline",
                                          help="Compare naive full-file baseline vs Code-RAG."),
) -> None:
    """Benchmark search latency (and optionally token savings vs a naive baseline)."""
    from coderag.evaluation.datasets import DEMO_DATASET, load_dataset
    from coderag.evaluation.harness import benchmark_latency
    from coderag.evaluation.harness import compare_baseline as _compare

    ds = dataset or DEMO_DATASET
    cases = load_dataset(ds)
    with session_scope() as session:
        from coderag.service import resolve_repository

        repo_obj = resolve_repository(session, repo)
        lat = benchmark_latency(session, repo_obj, cases)
        console.print(
            f"[bold]search latency[/] over {lat.n} runs: "
            f"p50 {lat.p50_ms:.2f}ms · p95 {lat.p95_ms:.2f}ms · mean {lat.mean_ms:.2f}ms"
        )
        if compare_baseline:
            cmp = _compare(session, repo_obj, cases)
            table = Table(title="Baseline vs Code-RAG (input tokens)")
            table.add_column("metric")
            table.add_column("tokens", justify="right")
            table.add_row("whole-repository baseline", f"{cmp.avg_baseline_tokens:.0f}")
            table.add_row("naive top-3 whole files", f"{cmp.avg_topfiles_tokens:.0f}")
            table.add_row("Code-RAG context tokens", f"{cmp.avg_rag_context_tokens:.0f}")
            table.add_row("Code-RAG full prompt tokens", f"{cmp.avg_rag_prompt_tokens:.0f}")
            console.print(table)
            console.print(
                f"[green]token reduction vs whole-repository baseline: "
                f"{cmp.token_reduction_percent}%[/]  "
                f"[dim](savings grow with repo size)[/]"
            )


@app.command()
def analyze(
    repo: str | None = typer.Option(None, "--repo"),
    tool: str = typer.Option("flake8", "--tool", help="flake8 or pylint"),
    fix: bool = typer.Option(False, "--fix", help="Propose LLM patches (needs a provider)."),
    limit: int = typer.Option(20, "--limit"),
) -> None:
    """Run a static analyzer, map findings to symbols, and build fix context."""
    from coderag.analyzers.flake8_adapter import Flake8Analyzer
    from coderag.analyzers.pylint_adapter import PylintAnalyzer
    from coderag.analyzers.workflow import build_fix_context, map_finding_to_symbol

    analyzer = PylintAnalyzer() if tool == "pylint" else Flake8Analyzer()
    if not analyzer.available():
        console.print(f"[red]{tool} is not installed[/] (pip install 'coderag[analyzers]')")
        raise typer.Exit(1)

    with session_scope() as session:
        from coderag.service import resolve_repository

        repo_obj = resolve_repository(session, repo)
        findings = analyzer.analyze(repo_obj.local_path)[:limit]
        table = Table(title=f"{tool} findings — {repo_obj.name}")
        table.add_column("finding")
        table.add_column("symbol", style="cyan")
        fixes = []
        for f in findings:
            sym = map_finding_to_symbol(session, repo_obj.id, f)
            table.add_row(f.describe(), sym.qualified_name if sym else "-")
            if fix:
                fixes.append(build_fix_context(session, repo_obj, f))
        console.print(table)
        if fix and fixes:
            from coderag.analyzers.workflow import run_fix_loop
            from coderag.llm.registry import get_llm_provider

            proposals = run_fix_loop(fixes, get_llm_provider(get_settings()))
            for p in proposals:
                console.print(f"\n[bold]{p['finding']}[/] → {p['symbol']}\n{p['patch']}")


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
