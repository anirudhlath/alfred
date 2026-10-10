from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from core.lazy import Lazy
from shared.config import DEFAULT_EMBEDDING_MODEL

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

    async def warmup(self) -> None:
        """Force any lazy initialization so the first real request doesn't pay for it.

        The default embeds one string, which is exactly what the services' warmup
        lambdas did. Backends with more to verify override this.
        """
        await self.embed("warmup")

    # B027: the empty body is the point — a concrete no-op default, not an
    # unimplemented abstract method. Backends that hold nothing need not override it.
    async def aclose(self) -> None:  # noqa: B027
        """Release any resources the backend holds. No-op unless a backend needs it.

        On the ABC so a service holding the ``EmbeddingProvider`` type can shut a
        provider down without knowing which backend it got — the HTTP backend owns a
        connection pool, the in-process one owns nothing.
        """


class SentenceTransformerProvider(EmbeddingProvider):
    """EmbeddingProvider backed by sentence-transformers."""

    def __init__(self, model_name: str = DEFAULT_EMBEDDING_MODEL) -> None:
        self._model_name = model_name
        # embed()/embed_batch() load the model via asyncio.to_thread — a startup
        # warmup racing the first request must not load it twice.
        self._model: Lazy[SentenceTransformer] = Lazy(self._load_model)
        # Force numpy's full initialization on the constructing (main) thread.
        # _load_model() imports sentence-transformers → torch → numpy inside a worker
        # thread (to_thread); if numpy is first imported there while the main
        # thread concurrently touches it (e.g. reindexing routines at startup),
        # numpy 2.x can raise a partial-init circular-import RecursionError.
        # Constructing a provider already implies the memory extra is installed.
        import numpy  # noqa: F401

    def _load_model(self) -> SentenceTransformer:
        from sentence_transformers import SentenceTransformer

        try:
            model: SentenceTransformer = SentenceTransformer(self._model_name)
            # Read dim off the model directly — self.dimension() would re-enter
            # self._model while it builds and deadlock on its (non-reentrant) lock.
            logger.info(
                "Loaded embedding model: %s (dim=%s)",
                self._model_name,
                model.get_sentence_embedding_dimension(),
            )
        except Exception as exc:
            # Expected when a gated model (e.g. google/embeddinggemma-300m) is
            # configured without HF_TOKEN + license acceptance. Memory embedding
            # is non-fatal (recall degrades, the system still runs), so keep this
            # a single actionable line rather than an alarming ERROR + traceback.
            # Re-raised, so nothing is cached and the next call tries again.
            logger.warning(
                "Embedding model %r unavailable (%s): memory recall disabled. "
                "Use an ungated model via EMBEDDING_MODEL (default %r needs no "
                "token), or set HF_TOKEN and accept the model's license.",
                self._model_name,
                type(exc).__name__,
                DEFAULT_EMBEDDING_MODEL,
            )
            raise
        return model

    def embed_sync(self, text: str) -> list[float]:
        model = self._model.get()
        arr = model.encode(text, normalize_embeddings=True)
        result: list[float] = arr.tolist()
        return result

    def embed_batch_sync(self, texts: list[str]) -> list[list[float]]:
        model = self._model.get()
        arr = model.encode(texts, normalize_embeddings=True)
        result: list[list[float]] = arr.tolist()
        return result

    async def embed(self, text: str) -> list[float]:
        return await asyncio.to_thread(self.embed_sync, text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self.embed_batch_sync, texts)

    def dimension(self) -> int:
        model = self._model.get()
        dim: int | None = model.get_sentence_embedding_dimension()
        if dim is None:
            raise RuntimeError(
                f"Embedding model {self._model_name!r} did not report an embedding dimension"
            )
        return dim

    def model_name(self) -> str:
        return self._model_name
