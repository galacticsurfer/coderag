"""Embedding provider abstraction.

No source code should need to leave company infrastructure to be embedded, so
every provider runs locally. The default (``HashingEmbeddingProvider``) is
deterministic and dependency-free; ``SentenceTransformerEmbeddingProvider`` uses
a local model. Embeddings are provider-independent: each stored vector records
its model, version, and dimension so the model can change and code can be
re-embedded (ADR-005 / schema notes).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    model_name: str
    model_version: str
    dimension: int

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of documents (structured symbol inputs)."""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """Embed a single query string."""


def build_embedding_input(
    repository_name: str,
    file_path: str,
    qualified_name: str,
    symbol_type: str,
    signature: str | None,
    docstring: str | None,
    source_code: str,
    max_code_chars: int = 4000,
) -> str:
    """Compose an embedding input that carries structural context (spec §9)."""
    code = source_code if len(source_code) <= max_code_chars else source_code[:max_code_chars]
    parts = [
        f"Repository: {repository_name}",
        f"File: {file_path}",
        f"Symbol: {qualified_name}",
        f"Type: {symbol_type}",
    ]
    if signature:
        parts.append(f"Signature: {signature}")
    if docstring:
        parts.append(f"Documentation: {docstring}")
    parts.append(f"Code:\n{code}")
    return "\n".join(parts)
