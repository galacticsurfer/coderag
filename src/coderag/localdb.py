"""Rootless local PostgreSQL (no Docker, no Homebrew, no sudo).

Uses ``pgserver``, which bundles a PostgreSQL build *with pgvector* and runs it
as your own user. This is the easiest way to get CodeRAG running on a laptop
(macOS or Linux) when you don't want to install Docker or a system Postgres.

The server keeps running after the command exits (``cleanup_mode=None``), so it
behaves like a background service you start once.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_PGDATA = Path.home() / ".coderag" / "pgdata"


def _require_pgserver():
    try:
        import pgserver  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - optional dependency
        import sys

        if sys.version_info >= (3, 13):
            raise RuntimeError(
                f"`coderag localdb` needs pgserver, which has no wheels for Python "
                f"{sys.version_info.major}.{sys.version_info.minor} (it supports 3.9–3.12).\n"
                "Either install CodeRAG on Python 3.12:\n"
                "    pipx install --python python3.12 'coderag-ai[mcp,localdb]'\n"
                "or run PostgreSQL yourself (Docker: `docker compose up -d db`, or "
                "Homebrew: `brew install postgresql@16 pgvector`) and set "
                "CODERAG_DATABASE_URL."
            ) from exc
        raise RuntimeError(
            "pgserver is not installed. Install the 'localdb' extra:\n"
            "    pipx install 'coderag-ai[mcp,localdb]'"
        ) from exc
    return pgserver


def sqlalchemy_url(libpq_uri: str) -> str:
    return libpq_uri.replace("postgresql://", "postgresql+psycopg://", 1)


def start(pgdata: Path | str | None = None) -> str:
    """Start (or attach to) the local server and return its SQLAlchemy URL."""
    pgserver = _require_pgserver()
    path = Path(pgdata or DEFAULT_PGDATA).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    # cleanup_mode=None -> the postmaster keeps running after we exit.
    server = pgserver.get_server(str(path), cleanup_mode=None)
    server.psql("CREATE EXTENSION IF NOT EXISTS vector;")
    return sqlalchemy_url(server.get_uri())


def stop(pgdata: Path | str | None = None, timeout: float = 15.0) -> None:
    """Stop the local server, and make sure it is actually stopped.

    ``pgserver`` reference-counts handles, so ``cleanup()`` alone is a no-op while
    another handle in this process is still open (e.g. one returned by ``start()``).
    We therefore verify, and fall back to signalling the postmaster directly.
    """
    import contextlib
    import os
    import signal
    import time

    pgserver = _require_pgserver()
    path = Path(pgdata or DEFAULT_PGDATA).expanduser()
    pid = status(path)
    # fall through to the direct signal below if this fails
    with contextlib.suppress(Exception):
        pgserver.get_server(str(path), cleanup_mode="stop").cleanup()

    if status(path) is None:
        return
    if pid is None:
        return
    # SIGINT = PostgreSQL "fast shutdown": roll back active transactions and exit.
    try:
        os.kill(pid, signal.SIGINT)
    except (OSError, ProcessLookupError):
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if status(path) is None:
            return
        time.sleep(0.2)


def status(pgdata: Path | str | None = None) -> int | None:
    """Return the postmaster PID if running, else None.

    Reads ``postmaster.pid`` directly and probes the process — deliberately does
    NOT go through ``get_server()``, which would *start* a stopped server.
    """
    import os

    path = Path(pgdata or DEFAULT_PGDATA).expanduser()
    pidfile = path / "postmaster.pid"
    if not pidfile.exists():
        return None
    try:
        pid = int(pidfile.read_text().splitlines()[0].strip())
    except (OSError, ValueError, IndexError):
        return None
    try:
        os.kill(pid, 0)  # signal 0 = existence check, does not affect the process
    except (OSError, ProcessLookupError):
        return None  # stale pid file
    return pid
