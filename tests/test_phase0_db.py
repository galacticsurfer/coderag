"""Phase 0: prove the DB layer, generated FTS column, and pgvector all work."""

from __future__ import annotations

import pytest
from sqlalchemy import select, text

pytestmark = pytest.mark.db


def test_extension_and_version(engine):
    with engine.connect() as conn:
        ver = conn.execute(
            text("SELECT extversion FROM pg_extension WHERE extname='vector'")
        ).scalar_one()
        assert ver  # pgvector present


def test_insert_symbol_and_fts_generated_column(db_session):
    from coderag.db.models import Repository, SourceFile, Symbol

    repo = Repository(name="payments", local_path="/tmp/payments")
    db_session.add(repo)
    db_session.flush()

    sf = SourceFile(
        repository_id=repo.id,
        path="services/payment_service.py",
        language="python",
        content_hash="deadbeef",
        size_bytes=100,
    )
    db_session.add(sf)
    db_session.flush()

    sym = Symbol(
        repository_id=repo.id,
        source_file_id=sf.id,
        file_path=sf.path,
        language="python",
        symbol_name="retry_payment",
        qualified_name="services.payment_service.PaymentService.retry_payment",
        symbol_type="method",
        signature="retry_payment(self, payment)",
        start_line=120,
        end_line=168,
        source_code="def retry_payment(self, payment):\n    ...",
        source_hash="cafe",
        token_count=42,
        search_document="retry_payment PaymentService retry failed payment invoice pending",
    )
    db_session.add(sym)
    db_session.commit()

    # The generated tsvector column must match a full-text query.
    row = db_session.execute(
        text(
            "SELECT symbol_name FROM symbols "
            "WHERE fts @@ plainto_tsquery('english', :q)"
        ),
        {"q": "retry payment"},
    ).first()
    assert row is not None and row[0] == "retry_payment"


def test_pgvector_roundtrip_and_cosine(db_session):
    from coderag.db.models import Repository, SourceFile, Symbol, SymbolEmbedding

    repo = Repository(name="p", local_path="/tmp/p")
    db_session.add(repo)
    db_session.flush()
    sf = SourceFile(
        repository_id=repo.id, path="a.py", language="python", content_hash="h"
    )
    db_session.add(sf)
    db_session.flush()

    ids = []
    for i, name in enumerate(["a", "b", "c"]):
        s = Symbol(
            repository_id=repo.id, source_file_id=sf.id, file_path="a.py",
            language="python", symbol_name=name, qualified_name=f"a.{name}",
            symbol_type="function", start_line=1, end_line=2, source_code="x",
            source_hash=f"h{i}", search_document=name,
        )
        db_session.add(s)
        db_session.flush()
        ids.append(s.id)
        db_session.add(
            SymbolEmbedding(
                repository_id=repo.id, symbol_id=s.id, source_hash=f"h{i}",
                embedding_model="test", embedding_version="1",
                embedding_dimension=3, embedding=[float(i), float(i), float(i)],
            )
        )
    db_session.commit()

    q = [0.0, 0.0, 0.1]  # closest to symbol "a" -> but a=[0,0,0] is zero vector
    # Use a non-zero target vector clearly nearest to "b"=[1,1,1].
    q = [1.0, 1.0, 0.9]
    nearest = db_session.execute(
        text(
            "SELECT s.symbol_name FROM symbol_embeddings e "
            "JOIN symbols s ON s.id = e.symbol_id "
            "ORDER BY e.embedding <=> CAST(:q AS vector) LIMIT 1"
        ),
        {"q": str(q)},
    ).scalar_one()
    assert nearest in {"b", "c"}  # both point same direction; not "a" (zero vec)


def test_orm_select_scoped_by_repository(db_session):
    from coderag.db.models import Repository, SourceFile, Symbol

    r1 = Repository(name="r1", local_path="/1")
    r2 = Repository(name="r2", local_path="/2")
    db_session.add_all([r1, r2])
    db_session.flush()
    for repo in (r1, r2):
        sf = SourceFile(repository_id=repo.id, path="a.py", language="python",
                        content_hash="h")
        db_session.add(sf)
        db_session.flush()
        db_session.add(Symbol(
            repository_id=repo.id, source_file_id=sf.id, file_path="a.py",
            language="python", symbol_name="secret", qualified_name="a.secret",
            symbol_type="function", start_line=1, end_line=1, source_code="x",
            source_hash="h", search_document="secret",
        ))
    db_session.commit()

    r1_syms = db_session.scalars(
        select(Symbol).where(Symbol.repository_id == r1.id)
    ).all()
    assert len(r1_syms) == 1
    assert all(s.repository_id == r1.id for s in r1_syms)
