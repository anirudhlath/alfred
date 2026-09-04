"""EmbeddingProvider backed by an OpenAI-compatible /v1/embeddings server.

The HTTP sibling of :class:`~core.memory.embedding_provider.SentenceTransformerProvider`,
behind the :mod:`core.memory.embedding_backend` seam. Points at any server exposing the
OpenAI embeddings API — vLLM started with ``--runner pooling`` is the reference case.

Why this exists: every service that touches memory constructs its own provider, so the
in-process backend loads one copy of the model (and of torch) per process. Talking to a
shared server collapses that to a single resident model.
"""

from __future__ import annotations

import logging

import httpx

from core.memory.embedding_provider import EmbeddingProvider

logger = logging.getLogger(__name__)

# Embedding calls are short; the ceiling is a large batch on a busy server.
_DEFAULT_TIMEOUT_SECONDS = 60.0


class OpenAICompatEmbeddingProvider(EmbeddingProvider):
    """EmbeddingProvider that POSTs to ``{host}/v1/embeddings``.

    ``dim`` is the configured dimension, returned by ``dimension()`` without any
    network call because the ABC's accessor is synchronous. ``warmup()`` is what
    proves the configuration true against the live server.
    """

    def __init__(
        self,
        model_name: str,
        host: str,
        dim: int,
        client: httpx.AsyncClient | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._model_name = model_name
        self._host = host.rstrip("/")
        self._dim = dim
        self._owns_client = client is None
        self._client = client if client is not None else httpx.AsyncClient(timeout=timeout)

    async def _post(self, texts: list[str]) -> list[list[float]]:
        resp = await self._client.post(
            f"{self._host}/v1/embeddings",
            json={"model": self._model_name, "input": texts},
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if len(data) != len(texts):
            raise RuntimeError(
                f"Embedding server returned {len(data)} embedding(s) for {len(texts)} "
                f"input(s) (model={self._model_name!r}, host={self._host!r})"
            )
        # The API is not required to preserve request order; ``index`` is
        # authoritative. Sorting on position instead would silently pair
        # embeddings with the wrong text.
        ordered = sorted(data, key=lambda item: int(item["index"]))
        return [[float(x) for x in item["embedding"]] for item in ordered]

    async def embed(self, text: str) -> list[float]:
        vectors = await self._post([text])
        return vectors[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            # vLLM rejects an empty input array with HTTP 400.
            return []
        return await self._post(texts)

    def dimension(self) -> int:
        return self._dim

    def model_name(self) -> str:
        return self._model_name

    async def warmup(self) -> None:
        """Prove the server is reachable AND agrees with the configured dimension.

        A dimension mismatch is silent everywhere else: the vector index would be
        built at one size and fed vectors of another, so recall degrades to nothing
        with no exception. Catch it once, here, with an actionable message.
        """
        actual = len(await self.embed("warmup"))
        if actual != self._dim:
            raise RuntimeError(
                f"Embedding server at {self._host!r} returns {actual}-dim vectors for "
                f"model {self._model_name!r}, but EMBEDDING_DIM is {self._dim}. Set "
                f"EMBEDDING_DIM={actual} (or correct EMBEDDING_MODEL) — a mismatch "
                f"silently breaks vector search."
            )
        logger.info(
            "Embedding backend ready: %s at %s (dim=%d)", self._model_name, self._host, actual
        )

    async def aclose(self) -> None:
        """Close the HTTP client if this provider created it."""
        if self._owns_client:
            await self._client.aclose()
