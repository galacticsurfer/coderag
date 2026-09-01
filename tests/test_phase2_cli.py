"""Phase 2: CLI index + search wiring (uses the test-configured engine)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from coderag.cli.main import app
from tests.conftest import DEMO_REPO_PATH

pytestmark = pytest.mark.db
runner = CliRunner()


def test_cli_index_then_search(engine, db_session):
    res = runner.invoke(app, ["index", DEMO_REPO_PATH, "--name", "payments"])
    assert res.exit_code == 0, res.output
    assert "Indexed" in res.output and "payments" in res.output

    res = runner.invoke(app, ["search", "retry_payment", "--repo", "payments"])
    assert res.exit_code == 0, res.output
    assert "Results in 'payments'" in res.output
    assert "retry" in res.output.lower()


def test_cli_search_unknown_repo_errors(engine, db_session):
    res = runner.invoke(app, ["search", "anything", "--repo", "does_not_exist"])
    assert res.exit_code != 0
