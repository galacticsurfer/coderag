"""Local SentenceTransformers embedding provider (optional `embeddings` extra).

Runs entirely on-box — no source code leaves your infrastructure. The model,
device, and batch size come from configuration.
"""

from __future__ import annotations

from coderag.embeddings.base import EmbeddingProvider


class SentenceTransformerEmbeddingProvider(EmbeddingProvider):
    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        device: str = "cpu",
        batch_size: int = 32,
    ) -> None:
        try:
            from sentence_transformers import SentenceTransformer  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "sentence-transformers is not installed. Install the 'embeddings' "
                "extra: pip install 'coderag-ai[embeddings]'"
            ) from exc
        self._model = SentenceTransformer(model_name, device=device)
        self.model_name = model_name
        self.model_version = "st1"
        self.batch_size = batch_size
        dim = self._model.get_sentence_embedding_dimension()
        if dim is None:  # pragma: no cover - model without a fixed dimension
            raise RuntimeError(
                f"model {model_name!r} does not report an embedding dimension"
            )
        self.dimension = int(dim)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [v.tolist() for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        vector = self._model.encode(
            [text], normalize_embeddings=True, convert_to_numpy=True,
            show_progress_bar=False,
        )[0]
        return vector.tolist()
