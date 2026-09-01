"""Phase 8: incremental git indexing — changed/added/deleted symbols."""

from __future__ import annotations

import subprocess

import pytest
from sqlalchemy import func, select

from coderag.db.models import Symbol, SymbolEmbedding
from coderag.indexing.indexer import Indexer, get_or_create_repository

pytestmark = pytest.mark.db


def _git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True,
                   capture_output=True, text=True)


def _write(root, rel, content):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def _init_repo(root):
    _git(root, "init")
    _git(root, "config", "user.email", "t@t.co")
    _git(root, "config", "user.name", "t")
    _write(root, "a.py", "def foo():\n    return 1\n\ndef bar():\n    return 2\n")
    _write(root, "d.py", "def dee():\n    return 4\n")
    _write(root, "b.py", "def baz():\n    return 3\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "c1")


def _quals(session, repo_id):
    return set(session.scalars(
        select(Symbol.qualified_name).where(Symbol.repository_id == repo_id)
    ))


def test_incremental_add_modify_delete(db_session, tmp_path):
    _init_repo(tmp_path)
    repo = get_or_create_repository(db_session, "inc", str(tmp_path))
    Indexer(db_session).full_index(repo)
    db_session.commit()

    quals = _quals(db_session, repo.id)
    assert "a.foo" in quals and "a.bar" in quals and "b.baz" in quals and "d.dee" in quals

    # capture d.dee's embedding row id (must be preserved across incremental)
    dee = db_session.scalar(select(Symbol).where(Symbol.qualified_name == "d.dee"))
    dee_emb_id = db_session.scalar(
        select(SymbolEmbedding.id).where(SymbolEmbedding.symbol_id == dee.id)
    )

    # commit 2: modify a.py (drop bar, add qux), delete b.py, add c.py; d.py unchanged
    _write(tmp_path, "a.py", "def foo():\n    return 1\n\ndef qux():\n    return 9\n")
    _git(tmp_path, "rm", "b.py")
    _write(tmp_path, "c.py", "def cee():\n    return 5\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "c2")

    _run, stats = Indexer(db_session).incremental_index(repo)
    db_session.commit()

    quals = _quals(db_session, repo.id)
    assert "a.qux" in quals           # added symbol
    assert "c.cee" in quals           # added file
    assert "a.bar" not in quals       # removed symbol
    assert "b.baz" not in quals       # deleted file
    assert "d.dee" in quals           # untouched file still present
    assert stats.files_deleted >= 1 and stats.symbols_deleted >= 1

    # unchanged file's embedding row was preserved (not re-embedded)
    dee2 = db_session.scalar(select(Symbol).where(Symbol.qualified_name == "d.dee"))
    assert dee2.id == dee.id
    dee_emb_id2 = db_session.scalar(
        select(SymbolEmbedding.id).where(SymbolEmbedding.symbol_id == dee2.id)
    )
    assert dee_emb_id2 == dee_emb_id

    # every symbol still has exactly one embedding
    n_sym = db_session.scalar(
        select(func.count()).select_from(Symbol).where(Symbol.repository_id == repo.id)
    )
    n_emb = db_session.scalar(
        select(func.count()).select_from(SymbolEmbedding).where(
            SymbolEmbedding.repository_id == repo.id
        )
    )
    assert n_sym == n_emb


def test_incremental_rebuilds_cross_file_edges(db_session, tmp_path):
    from coderag.db.models import SymbolRelationship
    from coderag.parsing.base import CALLS

    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@t.co")
    _git(tmp_path, "config", "user.name", "t")
    _write(tmp_path, "lib.py", "def helper():\n    return 1\n")
    _write(tmp_path, "app.py", "from lib import helper\n\ndef run():\n    return helper()\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "c1")
    repo = get_or_create_repository(db_session, "inc2", str(tmp_path))
    Indexer(db_session).full_index(repo)
    db_session.commit()

    # modify lib.py only; app.run's CALLS edge to lib.helper must survive rebuild
    _write(tmp_path, "lib.py", "def helper():\n    return 42\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "c2")
    Indexer(db_session).incremental_index(repo)
    db_session.commit()

    run = db_session.scalar(select(Symbol).where(Symbol.qualified_name == "app.run"))
    helper = db_session.scalar(select(Symbol).where(Symbol.qualified_name == "lib.helper"))
    edge = db_session.scalar(
        select(SymbolRelationship).where(
            SymbolRelationship.source_symbol_id == run.id,
            SymbolRelationship.target_symbol_id == helper.id,
            SymbolRelationship.relationship_type == CALLS,
        )
    )
    assert edge is not None  # cross-file edge from unchanged app.py rebuilt correctly


def _init_simple(root):
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@t.co")
    _git(root, "config", "user.name", "t")
    _write(root, "a.py", "def foo():\n    return 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "c1")


def test_incremental_falls_back_when_base_commit_is_unknown(db_session, tmp_path):
    """A bogus/missing base SHA must rebuild, not raise."""
    _init_simple(tmp_path)
    repo = get_or_create_repository(db_session, "fb1", str(tmp_path))
    Indexer(db_session).full_index(repo)
    db_session.commit()

    repo.indexed_commit_sha = "0" * 40  # commit that does not exist
    db_session.flush()

    run, stats = Indexer(db_session).incremental_index(repo)
    db_session.commit()
    assert run.mode == "full" and run.status == "success"
    assert "a.foo" in _quals(db_session, repo.id)


def test_incremental_falls_back_after_history_rewrite(db_session, tmp_path):
    """Amending the indexed commit orphans it; indexing must still succeed."""
    _init_simple(tmp_path)
    repo = get_or_create_repository(db_session, "fb2", str(tmp_path))
    Indexer(db_session).full_index(repo)
    db_session.commit()
    old_sha = repo.indexed_commit_sha

    # rewrite history so the indexed commit is unreachable, then prune it
    _write(tmp_path, "a.py", "def foo():\n    return 1\n\ndef bar():\n    return 2\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "--amend", "-m", "c1-amended")
    _git(tmp_path, "reflog", "expire", "--expire=now", "--all")
    _git(tmp_path, "gc", "--prune=now", "--quiet")

    from coderag.git.repo import GitRepo

    assert not GitRepo(str(tmp_path)).commit_exists(old_sha), "commit should be gone"

    run, stats = Indexer(db_session).incremental_index(repo)
    db_session.commit()
    assert run.mode == "full" and run.status == "success"
    quals = _quals(db_session, repo.id)
    assert {"a.foo", "a.bar"} <= quals  # amended content picked up
