from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    """Abstract embedding model interface."""

    @abstractmethod
    async def embed(self, text: str) -> list[float]: ...

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    def dimension(self) -> int: ...

    @abstractmethod
    def model_name(self) -> str: ...


class SentenceTransformerProvider(EmbeddingProvider):
    """EmbeddingProvider backed by sentence-transformers."""

    def __init__(self, model_name: str = "google/embeddinggemma-300m") -> None:
        self._model_name = model_name
        self._model: object | None = None

    def _load(self) -> object:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)
        return self._model

    def embed_sync(self, text: str) -> list[float]:
        model = self._load()
        arr = model.encode(text, normalize_embeddings=True)  # type: ignore[union-attr]
        return arr.tolist()  # type: ignore[union-attr]

    def embed_batch_sync(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        arr = model.encode(texts, normalize_embeddings=True)  # type: ignore[union-attr]
        return arr.tolist()  # type: ignore[union-attr]

    async def embed(self, text: str) -> list[float]:
        return await asyncio.to_thread(self.embed_sync, text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self.embed_batch_sync, texts)

    def dimension(self) -> int:
        model = self._load()
        dim: int = model.get_sentence_embedding_dimension()  # type: ignore[union-attr]
        return dim

    def model_name(self) -> str:
        return self._model_name
