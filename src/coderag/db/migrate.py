"""Run database migrations from an installed package.

The `alembic` console script and `alembic.ini` are only available in a source
checkout. When CodeRAG is installed as a wheel (pipx/pip), neither is on PATH, so
migrations are driven through Alembic's Python API with the migration directory
resolved from the installed package.
"""

from __future__ import annotations

from pathlib import Path

from coderag.core.config import get_settings


def migrations_path() -> Path:
    """Filesystem path of the packaged Alembic migration directory."""
    return Path(__file__).resolve().parent / "migrations"


def _config(url: str):
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(migrations_path()))
    # Escape '%' so ConfigParser interpolation doesn't choke on URL-encoded values.
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def upgrade_to_head(url: str | None = None) -> str:
    """Upgrade the database to the latest revision. Returns the URL used."""
    from alembic import command

    target = url or get_settings().database_url
    command.upgrade(_config(target), "head")
    return target


def current_revision(url: str | None = None) -> str | None:
    """Return the revision the database is currently stamped at (None if empty)."""
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine

    target = url or get_settings().database_url
    engine = create_engine(target)
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()
