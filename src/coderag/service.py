"""High-level operations shared by the CLI and the API.

Kept thin: resolve a repository, index, and run retrieval. Session management is
the caller's responsibility so these compose cleanly in both contexts.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from coderag.core.config import Settings, get_settings
from coderag.db.models import Repository
from coderag.indexing.indexer import IndexStats, index_repository
from coderag.retrieval.engine import RetrievalEngine, RetrievalOutcome, build_engine


class RepositoryNotFound(Exception):
    pass


class AmbiguousRepository(Exception):
    pass


def resolve_repository(session: Session, name: str | None = None) -> Repository:
    # fall back to a configured default (CODERAG_DEFAULT_REPOSITORY) when unnamed
    name = name or get_settings().default_repository
    if name:
        repo = session.scalar(select(Repository).where(Repository.name == name))
        if repo is None:
            raise RepositoryNotFound(f"no repository named {name!r}")
        return repo
    repos = list(session.scalars(select(Repository).order_by(Repository.id)))
    if not repos:
        raise RepositoryNotFound("no repositories indexed yet")
    if len(repos) > 1:
        names = ", ".join(r.name for r in repos)
        raise AmbiguousRepository(f"multiple repositories; pass --repo (one of: {names})")
    return repos[0]


def run_index(
    session: Session, path: str, name: str | None = None,
    settings: Settings | None = None, incremental: bool = False,
) -> tuple[Repository, IndexStats]:
    from coderag.indexing.indexer import Indexer, get_or_create_repository

    settings = settings or get_settings()
    p = Path(path).resolve()
    repo_name = name or p.name
    if not incremental:
        repo, _run, stats = index_repository(session, repo_name, str(p), settings=settings)
        return repo, stats
    repo = get_or_create_repository(session, repo_name, str(p))
    _run, stats = Indexer(session, settings=settings).incremental_index(repo)
    return repo, stats


def get_engine(
    settings: Settings | None = None, *, semantic: bool = False, graph: bool = False,
    rerank: bool = False,
) -> RetrievalEngine:
    settings = settings or get_settings()
    embedding_provider = None
    if semantic:
        from coderag.embeddings.registry import get_embedding_provider

        embedding_provider = get_embedding_provider(settings)
    return build_engine(settings, embedding_provider, with_graph=graph, with_reranker=rerank)


def run_search(
    session: Session, query: str, repo_name: str | None = None, top_n: int = 10,
    settings: Settings | None = None, *, semantic: bool = False, graph: bool = False,
    rerank: bool = False, record: bool = False,
) -> tuple[Repository, RetrievalOutcome]:
    settings = settings or get_settings()
    repo = resolve_repository(session, repo_name)
    engine = get_engine(settings, semantic=semantic, graph=graph, rerank=rerank)
    outcome = engine.search(session, repo.id, query, top_n=top_n)
    if record:
        from coderag.telemetry import record_query

        record_query(session, repo.id, query, "search", outcome.latency_ms,
                     candidates=outcome.candidates)
    return repo, outcome


def _retrieve_and_build(
    session, query, repo_name, settings, semantic, graph, max_tokens, finding,
    changed_symbol_ids,
):
    from coderag.context.builder import ContextBuilder

    repo = resolve_repository(session, repo_name)
    engine = get_engine(settings, semantic=semantic, graph=graph)
    outcome = engine.search(session, repo.id, query, top_n=None)
    builder = ContextBuilder(session, settings=settings)
    package = builder.build(
        query, outcome.candidates, repo,
        changed_symbol_ids=changed_symbol_ids, finding=finding, max_tokens=max_tokens,
    )
    return repo, package, outcome


def run_context(
    session: Session, query: str, repo_name: str | None = None,
    settings: Settings | None = None, *, semantic: bool = True, graph: bool = True,
    max_tokens: int | None = None, finding: str | None = None,
    changed_symbol_ids: set[int] | None = None, record: bool = True,
):
    """Retrieve, then build the exact context that WOULD be sent to the LLM."""
    settings = settings or get_settings()
    repo, package, outcome = _retrieve_and_build(
        session, query, repo_name, settings, semantic, graph, max_tokens, finding,
        changed_symbol_ids,
    )
    if record:
        from coderag.telemetry import record_query

        record_query(session, repo.id, query, "context", outcome.latency_ms,
                     package=package, candidates=outcome.candidates)
    return repo, package, outcome


def run_ask(
    session: Session, query: str, repo_name: str | None = None,
    settings: Settings | None = None, *, semantic: bool = True, graph: bool = True,
    max_tokens: int | None = None, max_output_tokens: int | None = None,
    finding: str | None = None, changed_symbol_ids: set[int] | None = None,
    provider=None,
):
    """Full pipeline: retrieve -> build context -> LLM -> answer, with accounting."""
    from coderag.llm.base import LLMRequest
    from coderag.llm.registry import get_llm_provider
    from coderag.telemetry import record_llm_request, record_query

    settings = settings or get_settings()
    provider = provider or get_llm_provider(settings)
    repo, package, outcome = _retrieve_and_build(
        session, query, repo_name, settings, semantic, graph, max_tokens, finding,
        changed_symbol_ids,
    )
    qrec = record_query(session, repo.id, query, "ask", outcome.latency_ms,
                        package=package, candidates=outcome.candidates)
    req = LLMRequest(
        prompt=package.prompt_text,
        max_tokens=max_output_tokens or settings.llm_max_output_tokens,
    )
    try:
        response = provider.generate(req)
    except Exception:
        record_llm_request(session, qrec.id, getattr(provider, "name", "unknown"),
                           provider.get_usage())
        raise
    record_llm_request(session, qrec.id, provider.name, response.usage)
    return repo, package, response, outcome
