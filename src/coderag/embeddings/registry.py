"""Embedding provider selection + a tiny cache keyed by configuration."""

from __future__ import annotations

from coderag.core.config import Settings, get_settings
from coderag.embeddings.base import EmbeddingProvider

_cache: dict[tuple, EmbeddingProvider] = {}


def get_embedding_provider(settings: Settings | None = None) -> EmbeddingProvider:
    settings = settings or get_settings()
    key = (
        settings.embedding_provider,
        settings.embedding_model,
        settings.embedding_device,
        settings.embedding_dimension,
    )
    if key in _cache:
        return _cache[key]

    if settings.embedding_provider == "sentence_transformer":
        from coderag.embeddings.sentence_transformer import (
            SentenceTransformerEmbeddingProvider,
        )

        provider: EmbeddingProvider = SentenceTransformerEmbeddingProvider(
            model_name=settings.embedding_model,
            device=settings.embedding_device,
            batch_size=settings.embedding_batch_size,
        )
    else:
        from coderag.embeddings.hashing import HashingEmbeddingProvider

        provider = HashingEmbeddingProvider(dimension=settings.embedding_dimension)

    _cache[key] = provider
    return provider
