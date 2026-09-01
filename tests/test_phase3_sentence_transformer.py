"""Phase 3 (slow, optional): the real local SentenceTransformers provider.

Skipped unless the `embeddings` extra is installed (and the model can be
downloaded). Verifies the provider embeds, reports a sane dimension, and ranks a
paraphrase above an unrelated sentence — i.e. genuine semantics, not just shared
tokens.
"""

from __future__ import annotations

import math

import pytest

pytestmark = pytest.mark.slow


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def test_sentence_transformer_semantics():
    pytest.importorskip("sentence_transformers")
    from coderag.embeddings.sentence_transformer import (
        SentenceTransformerEmbeddingProvider,
    )

    p = SentenceTransformerEmbeddingProvider()
    assert p.dimension > 0
    q = p.embed_query("code that retries failed transactions")
    related = p.embed_documents(["retry a payment that failed at the gateway"])[0]
    unrelated = p.embed_documents(["convert an image to grayscale"])[0]
    assert _cos(q, related) > _cos(q, unrelated)
