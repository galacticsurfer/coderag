"""Alembic environment.

The database URL comes from CODERAG settings / the environment (never hardcoded),
and the pgvector extension is created before any migration runs so that the
``vector`` type resolves.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import text

from coderag.core.config import get_settings
from coderag.db.base import make_engine
from coderag.db.models import Base

config = context.config
target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):
    """Ensure pgvector's Vector type renders with its import in migrations."""
    import pgvector.sqlalchemy

    if type_ == "type" and isinstance(obj, pgvector.sqlalchemy.Vector):
        autogen_context.imports.add("import pgvector.sqlalchemy")
        dim = getattr(obj, "dim", None)
        return f"pgvector.sqlalchemy.Vector({dim if dim else ''})"
    return False


def _url() -> str:
    # Allow an explicit override (used by tests), else fall back to settings.
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = make_engine_from_url(_url())
    with engine.connect() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_item=render_item,
        )
        with context.begin_transaction():
            context.run_migrations()


def make_engine_from_url(url: str):
    from sqlalchemy import create_engine

    return create_engine(url, future=True)


# Keep import used even if make_engine is unused directly.
_ = make_engine

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
