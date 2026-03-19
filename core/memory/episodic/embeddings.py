"""Embedding model wrapper for episodic memory vector search.

Uses a local sentence-transformer model. Embeddings are computed at
write time and stored as raw bytes alongside entries.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

# Model selected for accuracy/speed balance. Runs on CPU or GPU.
_DEFAULT_MODEL = "all-MiniLM-L6-v2"


class EmbeddingModel:
    """Wraps a sentence-transformer model for text embedding."""

    def __init__(self, model_name: str = _DEFAULT_MODEL) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)
        dim = self._model.get_sentence_embedding_dimension()
        if dim is None:
            msg = f"Model '{model_name}' did not report an embedding dimension"
            raise ValueError(msg)
        self._dim: int = dim
        logger.info("Loaded embedding model '%s' (dim=%d)", model_name, self._dim)

    @property
    def dimension(self) -> int:
        return int(self._dim)

    def embed(self, text: str) -> bytes:
        """Embed text and return as raw float32 bytes."""
        embedding = self._model.encode(text, convert_to_numpy=True)
        arr: np.ndarray[tuple[int], np.dtype[np.float32]] = np.asarray(embedding, dtype=np.float32)
        return arr.tobytes()

    @staticmethod
    def cosine_similarity(a: bytes, b: bytes) -> float:
        """Compute cosine similarity between two embedding byte arrays."""
        va = np.frombuffer(a, dtype=np.float32)
        vb = np.frombuffer(b, dtype=np.float32)
        dot = float(np.dot(va, vb))
        norm = float(np.linalg.norm(va) * np.linalg.norm(vb))
        if norm == 0:
            return 0.0
        return dot / norm
