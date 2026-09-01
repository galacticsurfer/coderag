"""Shared test fixtures.

A real PostgreSQL (with pgvector + full-text search) is provided by the
``pgserver`` package, which bundles the Postgres binaries and runs rootless —
no Docker required. This means our DB, FTS and vector tests exercise the exact
engine used in production, not a SQLite stand-in.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

# Force offline-safe defaults before anything imports settings.
os.environ.setdefault("CODERAG_EMBEDDING_PROVIDER", "hashing")
os.environ.setdefault("CODERAG_TOKEN_COUNTER", "heuristic")

# Configure robust (stream-resilient) logging once for the whole test session.
from coderag.core.logging import configure_logging  # noqa: E402

configure_logging("WARNING")


def _sqlalchemy_url(libpq_uri: str) -> str:
    return libpq_uri.replace("postgresql://", "postgresql+psycopg://", 1)


@pytest.fixture(scope="session")
def pg_uri() -> Iterator[str]:
    pgserver = pytest.importorskip("pgserver")
    pgdata = tempfile.mkdtemp(prefix="coderag-pg-")
    server = pgserver.get_server(pgdata, cleanup_mode="stop")
    try:
        yield _sqlalchemy_url(server.get_uri())
    finally:
        server.cleanup()


@pytest.fixture(scope="session")
def engine(pg_uri: str) -> Iterator[Engine]:
    from coderag.core.config import Settings
    from coderag.db.base import configure_engine, make_engine
    from coderag.db.models import Base

    eng = make_engine(Settings(database_url=pg_uri))
    with eng.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(eng)
    configure_engine(eng)
    yield eng
    eng.dispose()


DEMO_REPO_PATH = str(
    __import__("pathlib").Path(__file__).resolve().parents[1] / "examples" / "demo-repository"
)


@pytest.fixture
def demo_repo(db_session: Session):
    """Index the bundled demo 'payments' repository and return its Repository."""
    from coderag.indexing.indexer import index_repository

    repo, _run, _stats = index_repository(db_session, "payments", DEMO_REPO_PATH)
    db_session.commit()
    return repo


@pytest.fixture
def db_session(engine: Engine) -> Iterator[Session]:
    """A clean session per test; all tables truncated afterwards."""
    from coderag.db.base import get_session_factory
    from coderag.db.models import Base

    session = get_session_factory()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
        with engine.begin() as conn:
            conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
