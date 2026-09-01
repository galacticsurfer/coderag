"""Dev helper: boot a rootless pgserver, autogenerate the initial Alembic
migration, then verify it applies cleanly to a fresh database.

Run:  python scripts/gen_initial_migration.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

import pgserver
from sqlalchemy import create_engine, text


def sa_url(uri: str, db: str | None = None) -> str:
    url = uri.replace("postgresql://", "postgresql+psycopg://", 1)
    if db:
        # get_uri() form: postgresql://postgres:@/postgres?host=/sock
        url = url.replace("/postgres?", f"/{db}?")
    return url


def run(cmd: list[str], url: str) -> None:
    env = dict(os.environ, CODERAG_DATABASE_URL=url)
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, env=env)


def main() -> int:
    pgdata = tempfile.mkdtemp(prefix="coderag-genmig-")
    server = pgserver.get_server(pgdata, cleanup_mode="stop")
    try:
        base_uri = server.get_uri()
        # DB 1: autogenerate against an empty (extension-only) schema.
        gen_url = sa_url(base_uri)
        run(["alembic", "revision", "--autogenerate", "-m", "initial schema"], gen_url)

        # DB 2: fresh database, apply the migration end-to-end.
        server.psql("CREATE DATABASE migtest")
        apply_url = sa_url(base_uri, "migtest")
        run(["alembic", "upgrade", "head"], apply_url)

        eng = create_engine(apply_url)
        with eng.connect() as conn:
            tables = conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' ORDER BY table_name"
                )
            ).scalars().all()
        eng.dispose()
        print("Tables after migration:", tables)
        expected = {
            "repositories", "source_files", "symbols", "symbol_relationships",
            "symbol_embeddings", "indexing_runs", "queries", "retrieval_results",
            "llm_requests", "evaluation_runs",
        }
        missing = expected - set(tables)
        if missing:
            print("MISSING TABLES:", missing)
            return 1
        print("OK: migration applies cleanly and creates all tables.")
        return 0
    finally:
        server.cleanup()


if __name__ == "__main__":
    sys.exit(main())
