"""EmbeddingProvider backed by an OpenAI-compatible /v1/embeddings server.

The HTTP sibling of :class:`~core.memory.embedding_provider.SentenceTransformerProvider`,
behind the :mod:`core.memory.embedding_backend` seam. Points at any server exposing the
OpenAI embeddings API — vLLM started with ``--runner pooling`` is the reference case.

Why this exists: every service that touches memory constructs its own provider, so the
in-process backend loads one copy of the model (and of torch) per process. Talking to a
shared server collapses that to a single resident model.

Two behavioural differences from the in-process backend are worth knowing before
switching:

* **Over-long input.** ``sentence-transformers`` silently truncates at the model's max
  sequence length; a server hard-fails with HTTP 400 ("maximum context length is N
  tokens"). Errors here carry the server's own message so that is diagnosable.
* **Round trips.** ``EpisodicMemory.write`` (``core/memory/episodic/memory.py:41``),
  its migration path (``:150``) and ``ContextIndexManager.index_episodic``
  (``core/memory/context_index.py:47``) each ``asyncio.gather`` two ``embed()`` calls.
  In-process that is two cheap calls; here it is two HTTP round trips where a single
  ``embed_batch`` would do. They run concurrently, so the latency cost is small, but the
  request count is double.
"""

from __future__ import annotations

import logging

import httpx

from core.memory.embedding_provider import EmbeddingProvider
from shared.config import DEFAULT_EMBEDDING_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)
# Connect gets its own, much tighter budget: involuntary recall embeds the user's
# query inline in the reply path (core/conscious/engine.py:654), so an unreachable
# host must fail in seconds rather than hold a reply for the whole read budget.
_CONNECT_TIMEOUT_SECONDS = 5.0
# Server error bodies land in logs (core/memory/ingestor.py logs str(e) per event);
# enough for vLLM's message, short of pasting a proxy's HTML error page.
_MAX_ERROR_BODY_CHARS = 500


class OpenAICompatEmbeddingProvider(EmbeddingProvider):
    """EmbeddingProvider that POSTs to ``{host}/v1/embeddings``.

    ``dim`` is the configured dimension, returned by ``dimension()`` without any
    network call because the ABC's accessor is synchronous. The first response the
    provider ever parses is checked against it, and ``warmup()`` re-checks eagerly
    for an early signal at startup.

    ``timeout`` is applied per request, so it holds for an injected ``client`` too
    (that client's own default timeout is overridden). ``connect`` is pinned at
    ``_CONNECT_TIMEOUT_SECONDS`` regardless; ``timeout`` sets read/write/pool, and
    is tunable via ``EMBEDDING_TIMEOUT_SECONDS`` through the factory.
    """

    def __init__(
        self,
        model_name: str,
        host: str,
        dim: int,
        api_key: str = "",
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_EMBEDDING_TIMEOUT_SECONDS,
    ) -> None:
        self._model_name = model_name
        self._host = host.rstrip("/")
        self._dim = dim
        self._timeout = httpx.Timeout(
            connect=_CONNECT_TIMEOUT_SECONDS, read=timeout, write=timeout, pool=timeout
        )
        # Servers started with --api-key reject anonymous requests; sending an empty
        # bearer token to one that was not is worse than sending no header at all.
        # Strip once: a padded key sent verbatim 401s with nothing visible to blame.
        key = api_key.strip()
        self._headers = {"Authorization": f"Bearer {key}"} if key else {}
        self._owns_client = client is None
        self._client = client if client is not None else httpx.AsyncClient(timeout=self._timeout)

    def _failure(self, detail: str) -> RuntimeError:
        """One error shape for every failure: what broke, plus which server and model."""
        return RuntimeError(
            f"Embedding request failed: {detail} (model={self._model_name!r}, host={self._host!r})"
        )

    async def _post(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            # Both public callers already guard; this keeps the private path total.
            return []
        try:
            resp = await self._client.post(
                f"{self._host}/v1/embeddings",
                json={
                    "model": self._model_name,
                    "input": texts,
                    # Explicit: some servers default to base64 for large batches, which
                    # would arrive as a string where a list of floats is expected.
                    "encoding_format": "float",
                },
                headers=self._headers,
                timeout=self._timeout,
            )
        except httpx.RequestError as exc:
            # ConnectError's message is "[Errno 111] Connection refused" — it never
            # names the host, so the log line alone cannot tell you what to fix.
            raise self._failure(
                f"cannot reach the server ({type(exc).__name__}: {exc}). Is it running, "
                f"and is EMBEDDING_HOST correct?"
            ) from exc
        except RuntimeError as exc:
            # httpx raises a plain RuntimeError ("Cannot send a request, as the client
            # has been closed.") for use after aclose(). It is not a RequestError, so
            # without this it would escape with no host or model attached. Nothing
            # inside the try block raises our own RuntimeError, so this cannot swallow one.
            raise self._failure(f"the HTTP client is unusable ({exc})") from exc
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # raise_for_status() reports only the status code and an MDN link, while the
            # body carries the actionable part ("The model 'x' does not exist.", "maximum
            # context length is 8192 tokens"). Keep it, bounded.
            body = resp.text[:_MAX_ERROR_BODY_CHARS].strip()
            raise self._failure(f"server returned HTTP {resp.status_code}: {body}") from exc
        return self._parse(resp, texts)

    def _parse(self, resp: httpx.Response, texts: list[str]) -> list[list[float]]:
        """Turn a 200 response into vectors, treating every shape surprise as an error.

        A 200 is not a guarantee of the OpenAI schema: a proxy can return HTML, and a
        malformed item would otherwise surface as a bare ``KeyError``/``ValueError``
        with no clue which server produced it.
        """
        try:
            payload = resp.json()
        except ValueError as exc:
            raise self._failure(f"response body was not JSON ({exc})") from exc
        if not isinstance(payload, dict):
            raise self._failure(f"response was a JSON {type(payload).__name__}, expected an object")
        data = payload.get("data")
        if not isinstance(data, list):
            raise self._failure("response has no 'data' array")
        if len(data) != len(texts):
            raise self._failure(
                f"server returned {len(data)} embedding(s) for {len(texts)} input(s)"
            )
        try:
            ordered = sorted(data, key=lambda item: int(item["index"]))
            indices = [int(item["index"]) for item in ordered]
        except (KeyError, TypeError, ValueError) as exc:
            raise self._failure(
                f"an item has no usable 'index' ({type(exc).__name__}: {exc})"
            ) from exc
        # The API is not required to preserve request order; ``index`` is authoritative.
        # Sorting is stable, so a repeated index would survive the length check above and
        # silently pair an embedding with the wrong text — demand a clean permutation.
        if indices != list(range(len(texts))):
            raise self._failure(
                f"item indices {indices} are not 0..{len(texts) - 1} (duplicate or out of range)"
            )
        vectors: list[list[float]] = []
        for item in ordered:
            vector = item.get("embedding")
            if not isinstance(vector, list):
                # Covers both a missing key and a base64 string body.
                raise self._failure(
                    f"an item's 'embedding' is {type(vector).__name__}, expected a list of floats"
                )
            # Every vector, not just the first: a ragged batch would otherwise pass.
            self._verify_dim(len(vector))
            # Values are used as-is: json already decoded them, and re-running float()
            # over every element costs ~2.1M conversions on a 2048-item bge-m3 batch.
            # An int-valued element (JSON "0" rather than "0.0") is accepted by every
            # consumer — struct.pack("<Nf") and numpy both take ints for float slots.
            vectors.append(vector)
        return vectors

    def _verify_dim(self, actual: int) -> None:
        """Check the served width against the configured one, on every vector.

        This lives on the request path on purpose. ``warmup()`` is best-effort —
        ``core/warmup.py`` logs a warning and continues — so a guard that only ran there
        would let a mismatched service carry on writing wrong-width vectors into the
        index, which is exactly the silent corruption this backend makes easy.

        It is never cached, either: the shared server can be restarted onto a different
        model while a service holds this provider for days, and an ``int`` comparison
        costs nothing next to the round trip that produced the vector. Downstream is no
        safety net — ``RedisVectorStore.add()`` packs and HSETs whatever width it gets.
        """
        if actual != self._dim:
            raise RuntimeError(
                f"Embedding server at {self._host!r} returns {actual}-dim vectors for "
                f"model {self._model_name!r}, but EMBEDDING_DIM is {self._dim}. Set "
                f"EMBEDDING_DIM={actual} (or correct EMBEDDING_MODEL) — a mismatch "
                f"silently breaks vector search."
            )

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

        The width check itself is on the request path (every response passes through
        ``_verify_dim``), so this adds no guarantee — it moves the signal earlier,
        turning a misconfiguration into a startup log line rather than a surprise at
        the first real memory write.
        """
        actual = len(await self.embed("warmup"))
        logger.info(
            "Embedding backend ready: %s at %s (dim=%d)", self._model_name, self._host, actual
        )

    async def aclose(self) -> None:
        """Close the HTTP client if this provider created it.

        An injected client belongs to its caller — closing it here would break every
        other user of a shared pool.
        """
        if self._owns_client:
            await self._client.aclose()
