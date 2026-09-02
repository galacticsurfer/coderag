"""Rootless local Postgres helper (no Docker/sudo) — lifecycle correctness."""

from __future__ import annotations

import pytest

pytest.importorskip("pgserver")

from coderag import localdb  # noqa: E402


def test_status_does_not_start_the_server(tmp_path):
    """A status check must never start a server (regression: it used to)."""
    pgdata = tmp_path / "pgdata"
    assert localdb.status(pgdata) is None       # not running, nothing created
    assert not (pgdata / "postmaster.pid").exists()


def test_start_status_stop_lifecycle(tmp_path):
    pgdata = tmp_path / "pgdata"
    url = localdb.start(pgdata)
    try:
        assert url.startswith("postgresql+psycopg://")
        pid = localdb.status(pgdata)
        assert pid and pid > 0
    finally:
        localdb.stop(pgdata)
    assert localdb.status(pgdata) is None       # stays stopped


def test_stale_pidfile_reports_not_running(tmp_path):
    pgdata = tmp_path / "pgdata"
    pgdata.mkdir()
    (pgdata / "postmaster.pid").write_text("999999999\n")  # impossible pid
    assert localdb.status(pgdata) is None


def test_start_does_not_shell_out_to_psql(tmp_path, monkeypatch):
    """Regression: the bundled `psql` binary can fail on macOS (exit 127).

    `start()` must create the pgvector extension over a normal DB connection, so
    the command works even when that executable is unusable.
    """
    import pgserver

    real_get_server = pgserver.get_server

    def guarded(pgdata, cleanup_mode=None):
        server = real_get_server(pgdata, cleanup_mode=cleanup_mode)

        def boom(*a, **k):
            raise AssertionError("start() must not invoke the bundled psql binary")

        monkeypatch.setattr(server, "psql", boom, raising=False)
        return server

    monkeypatch.setattr(pgserver, "get_server", guarded)

    pgdata = tmp_path / "pgdata"
    url = localdb.start(pgdata)
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(url)
        with engine.connect() as conn:
            got = conn.execute(
                text("SELECT extversion FROM pg_extension WHERE extname='vector'")
            ).scalar()
        engine.dispose()
        assert got, "pgvector extension should have been created"
    finally:
        localdb.stop(pgdata)


def test_start_records_url_only_for_default_pgdata(tmp_path, monkeypatch):
    """A non-default --pgdata must not clobber the recorded default URL."""
    from coderag import localdb as m

    monkeypatch.setattr(m, "URL_FILE", tmp_path / "database_url")
    pgdata = tmp_path / "custom"
    url = m.start(pgdata)
    try:
        assert not (tmp_path / "database_url").exists()  # custom dir -> not recorded
        assert url.startswith("postgresql+psycopg://")
    finally:
        m.stop(pgdata)


def test_setup_helpers_are_idempotent(tmp_path):
    from coderag.setup_flow import append_nudge, claude_md_needs_nudge

    target = tmp_path / "CLAUDE.md"
    target.write_text("# My project\n\nExisting notes.\n")
    assert claude_md_needs_nudge(target)
    append_nudge(target)
    body = target.read_text()
    assert "Existing notes." in body           # never clobbers existing content
    assert body.count("## Code search") == 1
    assert not claude_md_needs_nudge(target)   # second run is a no-op
