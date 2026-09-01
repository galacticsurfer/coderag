"""Phase 1: indexing persists symbols, skips secrets/ignored, reindexes cleanly."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from coderag.db.models import SourceFile, Symbol
from coderag.indexing.indexer import index_repository

pytestmark = pytest.mark.db


def _write(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def _make_repo(root: Path) -> None:
    _write(root, "pkg/__init__.py", "")
    _write(
        root,
        "pkg/service.py",
        "class Service:\n"
        "    def run(self, x):\n"
        "        return x\n\n"
        "def helper():\n"
        "    return 1\n",
    )
    # must be skipped: secret file, ignored dir, binary
    _write(root, ".env", "API_KEY=supersecretvalue123456\n")
    _write(root, "node_modules/dep.py", "def evil(): return 1\n")
    _write(root, "assets/logo.png", "\x89PNG\x00binary")


def test_index_persists_expected_symbols(db_session, tmp_path):
    _make_repo(tmp_path)
    repo, run, stats = index_repository(db_session, "demo", str(tmp_path))
    db_session.commit()

    assert run.status == "success"
    quals = set(
        db_session.scalars(
            select(Symbol.qualified_name).where(Symbol.repository_id == repo.id)
        )
    )
    assert "pkg.service.Service" in quals
    assert "pkg.service.Service.run" in quals
    assert "pkg.service.helper" in quals
    assert "pkg.service" in quals  # module symbol


def test_secret_and_ignored_files_not_indexed(db_session, tmp_path):
    _make_repo(tmp_path)
    repo, run, stats = index_repository(db_session, "demo", str(tmp_path))
    db_session.commit()

    paths = set(
        db_session.scalars(
            select(SourceFile.path).where(SourceFile.repository_id == repo.id)
        )
    )
    assert ".env" not in paths
    assert not any("node_modules" in p for p in paths)
    assert not any(p.endswith(".png") for p in paths)
    # only the two python source files under pkg/
    assert paths == {"pkg/__init__.py", "pkg/service.py"}


def test_token_count_populated(db_session, tmp_path):
    _make_repo(tmp_path)
    repo, *_ = index_repository(db_session, "demo", str(tmp_path))
    db_session.commit()
    run_sym = db_session.scalar(
        select(Symbol).where(Symbol.qualified_name == "pkg.service.Service.run")
    )
    assert run_sym.token_count > 0
    assert run_sym.start_line >= 1 and run_sym.end_line >= run_sym.start_line


def test_reindex_replaces_and_removes_deleted_symbols(db_session, tmp_path):
    _make_repo(tmp_path)
    repo, *_ = index_repository(db_session, "demo", str(tmp_path))
    db_session.commit()
    first_count = db_session.scalar(
        select(func.count()).select_from(Symbol).where(Symbol.repository_id == repo.id)
    )

    # Modify: remove helper(), add other()
    _write(
        tmp_path,
        "pkg/service.py",
        "class Service:\n    def run(self, x):\n        return x\n\n"
        "def other():\n    return 2\n",
    )
    index_repository(db_session, "demo", str(tmp_path))
    db_session.commit()

    quals = set(
        db_session.scalars(
            select(Symbol.qualified_name).where(Symbol.repository_id == repo.id)
        )
    )
    assert "pkg.service.other" in quals
    assert "pkg.service.helper" not in quals  # deleted symbol gone
    # no duplicate accumulation across reindex
    second_count = db_session.scalar(
        select(func.count()).select_from(Symbol).where(Symbol.repository_id == repo.id)
    )
    assert second_count == first_count  # same number of symbols (helper->other)


def test_redaction_removes_secret_value_from_stored_code(db_session, tmp_path):
    _write(
        tmp_path,
        "cfg.py",
        'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n'
        'password = "hunter2wordlongenough"\n'
        "def f():\n    return 1\n",
    )
    repo, *_ = index_repository(db_session, "demo", str(tmp_path))
    db_session.commit()
    module = db_session.scalar(
        select(Symbol).where(Symbol.qualified_name == "cfg")
    )
    assert "AKIAIOSFODNN7EXAMPLE" not in module.source_code
    assert "hunter2wordlongenough" not in module.source_code
    assert "REDACTED" in module.source_code
