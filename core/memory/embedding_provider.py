from __future__ import annotations

import asyncio
import logging
import threading
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)


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
        self._model: SentenceTransformer | None = None
        # embed()/embed_batch() run _load() via asyncio.to_thread — a startup
        # warmup racing the first request must not load the model twice.
        self._load_lock = threading.Lock()
        # Force numpy's full initialization on the constructing (main) thread.
        # _load() imports sentence-transformers → torch → numpy inside a worker
        # thread (to_thread); if numpy is first imported there while the main
        # thread concurrently touches it (e.g. reindexing routines at startup),
        # numpy 2.x can raise a partial-init circular-import RecursionError.
        # Constructing a provider already implies the memory extra is installed.
        import numpy  # noqa: F401

    def _load(self) -> SentenceTransformer:
        with self._load_lock:
            if self._model is None:
                from sentence_transformers import SentenceTransformer

                try:
                    self._model = SentenceTransformer(self._model_name)
                    # Read dim off the model directly — self.dimension() would
                    # re-enter _load() and deadlock on the (non-reentrant) lock.
                    logger.info(
                        "Loaded embedding model: %s (dim=%s)",
                        self._model_name,
                        self._model.get_sentence_embedding_dimension(),
                    )
                except Exception:
                    logger.error(
                        "Failed to load embedding model %s",
                        self._model_name,
                        exc_info=True,
                    )
                    raise
            return self._model

    def embed_sync(self, text: str) -> list[float]:
        model = self._load()
        arr = model.encode(text, normalize_embeddings=True)
        result: list[float] = arr.tolist()
        return result

    def embed_batch_sync(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        arr = model.encode(texts, normalize_embeddings=True)
        result: list[list[float]] = arr.tolist()
        return result

    async def embed(self, text: str) -> list[float]:
        return await asyncio.to_thread(self.embed_sync, text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self.embed_batch_sync, texts)

    def dimension(self) -> int:
        model = self._load()
        dim: int | None = model.get_sentence_embedding_dimension()
        if dim is None:
            raise RuntimeError(
                f"Embedding model {self._model_name!r} did not report an embedding dimension"
            )
        return dim

    def model_name(self) -> str:
        return self._model_name
