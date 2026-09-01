"""Migrations must work from an installed wheel (no alembic.ini, no CLI on PATH)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text

from coderag.db.migrate import current_revision, migrations_path, upgrade_to_head

pytestmark = pytest.mark.db


def test_migrations_are_packaged():
    """The migration scripts ship inside the package, not just the source tree."""
    path = migrations_path()
    assert (path / "env.py").is_file()
    versions = list((path / "versions").glob("*.py"))
    assert versions, "no migration revisions packaged"


def test_upgrade_to_head_without_alembic_ini(pg_uri: str):
    """Regression: `localdb start` used to shell out to an `alembic` binary that
    isn't on PATH in a pipx install. Migrations now run via the Python API."""
    dbname = "migapi_" + uuid.uuid4().hex[:8]
    admin = create_engine(pg_uri, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    admin.dispose()

    url = pg_uri.replace("/postgres?", f"/{dbname}?")
    assert current_revision(url) is None          # fresh database
    upgrade_to_head(url)
    assert current_revision(url) is not None      # stamped after upgrade

    eng = create_engine(url)
    with eng.connect() as conn:
        tables = set(conn.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
        )).scalars())
    eng.dispose()
    assert {"repositories", "symbols", "symbol_embeddings"} <= tables
