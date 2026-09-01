"""Semantic retrieval via pgvector cosine search.

Embeds the query with the same provider used at index time and finds the nearest
symbol embeddings (cosine). Scoped by repository and by embedding model/version
so results never mix vectors from different models. Exact scan for correctness
(HNSW is a later, benchmark-driven optimisation — ADR-001).
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from coderag.core.config import Settings, get_settings
from coderag.embeddings.base import EmbeddingProvider
from coderag.retrieval.base import SEMANTIC, RawHit


class SemanticRetriever:
    source = SEMANTIC

    def __init__(
        self, provider: EmbeddingProvider, settings: Settings | None = None
    ) -> None:
        self.provider = provider
        self.settings = settings or get_settings()

    def retrieve(
        self, session: Session, repository_id: int, query: str, limit: int
    ) -> list[RawHit]:
        vec = self.provider.embed_query(query)
        rows = session.execute(
            text(
                """
                SELECT symbol_id, embedding <=> CAST(:q AS vector) AS distance
                FROM symbol_embeddings
                WHERE repository_id = :rid
                  AND embedding_model = :model
                  AND embedding_version = :version
                ORDER BY embedding <=> CAST(:q AS vector) ASC
                LIMIT :limit
                """
            ),
            {
                "q": str(vec),
                "rid": repository_id,
                "model": self.provider.model_name,
                "version": self.provider.model_version,
                "limit": limit,
            },
        ).all()
        # cosine distance in [0,2] -> similarity score in [-1,1]
        return [RawHit(symbol_id=r[0], score=1.0 - float(r[1])) for r in rows]
