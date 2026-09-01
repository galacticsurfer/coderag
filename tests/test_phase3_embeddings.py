"""Phase 3: hashing embedding provider (deterministic, offline)."""

from __future__ import annotations

import math

from coderag.embeddings.base import build_embedding_input
from coderag.embeddings.hashing import HashingEmbeddingProvider


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def test_dimension_and_determinism():
    p = HashingEmbeddingProvider(dimension=128)
    assert p.dimension == 128
    v1 = p.embed_query("retry failed payment")
    v2 = p.embed_query("retry failed payment")
    assert v1 == v2
    assert len(v1) == 128


def test_vectors_are_normalised():
    p = HashingEmbeddingProvider()
    v = p.embed_query("PaymentService retry_payment invoice pending")
    assert abs(math.sqrt(sum(x * x for x in v)) - 1.0) < 1e-6


def test_shared_vocabulary_scores_higher():
    p = HashingEmbeddingProvider()
    q = p.embed_query("retry failed payment transaction")
    related = p.embed_documents(["retry payment failed attempts invoice"])[0]
    unrelated = p.embed_documents(["parse abstract syntax tree tokenizer"])[0]
    assert _cos(q, related) > _cos(q, unrelated)


def test_build_embedding_input_has_structure():
    text = build_embedding_input(
        "payments", "services/p.py", "p.PaymentService.retry_payment", "method",
        "retry_payment(self, payment)", "Retry a payment.", "def retry_payment(): ...",
    )
    assert "Repository: payments" in text
    assert "Symbol: p.PaymentService.retry_payment" in text
    assert "Type: method" in text
    assert "Signature: retry_payment(self, payment)" in text
    assert "Documentation: Retry a payment." in text
    assert "Code:" in text
