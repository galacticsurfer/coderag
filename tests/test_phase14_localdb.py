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
