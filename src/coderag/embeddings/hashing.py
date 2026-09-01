"""Deterministic, offline embedding provider (default).

Uses signed feature hashing over word-expanded identifiers: each token is hashed
to a bucket with a +/- sign, summed, and L2-normalised. This is NOT a learned
semantic model — similarity reflects shared vocabulary — but it is fully offline,
deterministic, and dependency-free, which makes it ideal for tests, CI, and
air-gapped dev. For genuine semantic matching use
``SentenceTransformerEmbeddingProvider``.
"""

from __future__ import annotations

import hashlib
import math

from coderag.core.text import expand_query_terms
from coderag.embeddings.base import EmbeddingProvider


class HashingEmbeddingProvider(EmbeddingProvider):
    model_version = "1"

    def __init__(self, dimension: int = 384) -> None:
        self.dimension = dimension
        self.model_name = f"hashing-{dimension}"

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dimension
        for token in expand_query_terms(text):
            digest = hashlib.md5(token.lower().encode("utf-8")).digest()  # noqa: S324
            idx = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if (digest[4] & 1) else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)
