"""Phase 0: the Alembic migration must apply cleanly to a fresh database."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.db

EXPECTED = {
    "repositories", "source_files", "symbols", "symbol_relationships",
    "symbol_embeddings", "indexing_runs", "queries", "retrieval_results",
    "llm_requests", "evaluation_runs",
}


def test_alembic_upgrade_head_creates_all_tables(pg_uri: str):
    from alembic import command
    from alembic.config import Config

    dbname = "mig_" + uuid.uuid4().hex[:8]
    admin = create_engine(pg_uri, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    admin.dispose()

    target_url = pg_uri.replace("/postgres?", f"/{dbname}?")

    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "src/coderag/db/migrations")
    cfg.set_main_option("sqlalchemy.url", target_url)
    command.upgrade(cfg, "head")

    eng = create_engine(target_url)
    with eng.connect() as conn:
        tables = set(
            conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public'"
                )
            ).scalars()
        )
    eng.dispose()
    assert tables >= EXPECTED, f"missing: {EXPECTED - tables}"
