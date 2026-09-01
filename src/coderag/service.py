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
    settings: Settings | None = None,
) -> tuple[Repository, IndexStats]:
    settings = settings or get_settings()
    p = Path(path).resolve()
    repo_name = name or p.name
    repo, _run, stats = index_repository(session, repo_name, str(p), settings=settings)
    return repo, stats


def get_engine(
    settings: Settings | None = None, *, semantic: bool = False, graph: bool = False
) -> RetrievalEngine:
    settings = settings or get_settings()
    embedding_provider = None
    if semantic:
        from coderag.embeddings.registry import get_embedding_provider

        embedding_provider = get_embedding_provider(settings)
    return build_engine(settings, embedding_provider, with_graph=graph)


def run_search(
    session: Session, query: str, repo_name: str | None = None, top_n: int = 10,
    settings: Settings | None = None, *, semantic: bool = False, graph: bool = False,
) -> tuple[Repository, RetrievalOutcome]:
    settings = settings or get_settings()
    repo = resolve_repository(session, repo_name)
    engine = get_engine(settings, semantic=semantic, graph=graph)
    outcome = engine.search(session, repo.id, query, top_n=top_n)
    return repo, outcome
