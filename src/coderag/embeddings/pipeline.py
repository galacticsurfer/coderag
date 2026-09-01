"""Embedding pipeline: embed a repository's symbols, cached by source hash.

Unchanged code is never re-embedded: an embedding is (re)computed only when no
row exists for (symbol, model, version) or the symbol's ``source_hash`` changed.
Embedding inputs carry structural context (repo/file/symbol/type/signature/
docstring/code), not just the raw body (spec §9).
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from coderag.core.logging import get_logger
from coderag.db.models import Repository, Symbol, SymbolEmbedding
from coderag.embeddings.base import EmbeddingProvider, build_embedding_input

log = get_logger("embeddings")


class EmbeddingPipeline:
    def __init__(self, session: Session, provider: EmbeddingProvider) -> None:
        self.session = session
        self.provider = provider

    def embed_repository(self, repo: Repository) -> int:
        model = self.provider.model_name
        version = self.provider.model_version

        existing: dict[int, str] = {
            row[0]: row[1]
            for row in self.session.execute(
                select(SymbolEmbedding.symbol_id, SymbolEmbedding.source_hash).where(
                    SymbolEmbedding.repository_id == repo.id,
                    SymbolEmbedding.embedding_model == model,
                    SymbolEmbedding.embedding_version == version,
                )
            ).all()
        }

        symbols = list(
            self.session.scalars(
                select(Symbol).where(Symbol.repository_id == repo.id)
            )
        )
        stale: list[Symbol] = [
            s for s in symbols if existing.get(s.id) != s.source_hash
        ]
        if not stale:
            return 0

        # Drop any prior (stale) rows for these symbols before re-inserting.
        stale_ids = [s.id for s in stale]
        self.session.execute(
            delete(SymbolEmbedding).where(
                SymbolEmbedding.symbol_id.in_(stale_ids),
                SymbolEmbedding.embedding_model == model,
                SymbolEmbedding.embedding_version == version,
            )
        )

        inputs = [
            build_embedding_input(
                repo.name, s.file_path, s.qualified_name, s.symbol_type,
                s.signature, s.docstring, s.source_code,
            )
            for s in stale
        ]
        vectors = self.provider.embed_documents(inputs)
        for s, vec in zip(stale, vectors, strict=True):
            self.session.add(
                SymbolEmbedding(
                    repository_id=repo.id,
                    symbol_id=s.id,
                    source_hash=s.source_hash,
                    embedding_model=model,
                    embedding_version=version,
                    embedding_dimension=self.provider.dimension,
                    embedding=vec,
                )
            )
        self.session.flush()
        log.info(
            "embed_repository.done", repository=repo.name, model=model,
            embedded=len(stale), skipped=len(symbols) - len(stale),
        )
        return len(stale)
