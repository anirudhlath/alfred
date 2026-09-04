# vLLM Embedding Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Alfred get embeddings from an existing OpenAI-compatible embedding server (vLLM serving `BAAI/bge-m3`) instead of every service loading its own in-process `sentence-transformers` model.

**Architecture:** `EmbeddingProvider` is already an ABC, so this adds a second implementation — `OpenAICompatEmbeddingProvider` — that POSTs to `/v1/embeddings`, plus a `build_embedding_provider()` factory selected by `EMBEDDING_BACKEND` (`sentence_transformers` default | `openai`). This mirrors the existing `REFLEX_BACKEND` → `core/reflex/inference.py` seam exactly. Switching backends changes the embedding model, and therefore the vector dimension (384/768 → 1024), so both vector stores gain a dimension guard that fails loudly instead of silently mismatching an existing index.

**Tech Stack:** Python 3.13, `httpx` (already a base dep), Pydantic v2 config, pytest + `httpx.MockTransport`, `mypy --strict`, ruff (line-length 100).

**Why:** Four separate processes (`core/conscious/__main__.py`, `core/channels/admin_api.py`, `core/memory/ingestor_main.py`, `core/librarian/__main__.py`) each construct their own `SentenceTransformerProvider`, so each loads its own copy of the model and of torch. On the deployment box a vLLM server already hosts `BAAI/bge-m3` (`--runner pooling`, ~1.8GB VRAM) at port 8001. This is the "externalize embedding" alternative anticipated in `docs/backlog/medium/cpu-only-torch-index.md` ("If GPU-accelerated in-container inference is ever wanted … this decision would need revisiting"), and it gives `docs/backlog/high/embedding-model-gated-first-run.md` a gate-free path that needs no HF token at all.

**Explicitly out of scope:** removing `sentence-transformers`/`torch` from the `memory` extra. Sentence-transformers stays the default backend and the offline fallback so a fresh clone still works with no embedding server running.

---

## Environment facts (verified 2026-09-03)

Confirmed by probing the running server; a task that contradicts these is wrong.

- Endpoint: `http://localhost:8001/v1/embeddings` (container `vllm-embed`, published `8001->8000`).
- `GET /v1/models` returns one entry: `id` = `BAAI/bge-m3`, `max_model_len` = 8192.
- `POST /v1/embeddings` with `{"model": "BAAI/bge-m3", "input": ["a", "b"]}` returns
  `{"object", "data": [{"index", "embedding"}], "usage": {...}}` with `len(data) == 2` and
  `len(data[0]["embedding"]) == 1024`.
- The main chat vLLM on port 8000 (`gemma-4-26b-a4b`) returns **404** for `/v1/embeddings` —
  the two servers are separate and only 8001 embeds.
- The live `idx:context` RediSearch index currently reports `num_docs: 0` at dim 768, so
  there is no production vector data to re-embed today. The dimension guards in Tasks 5
  and 6 exist so this stays safe once the index is populated.

---

## File Structure

**Create:**
- `core/memory/openai_embedding_provider.py` — `OpenAICompatEmbeddingProvider`, the HTTP adapter. One responsibility: turn text into vectors over `/v1/embeddings`.
- `core/memory/embedding_backend.py` — `build_embedding_provider(config)`, the backend seam. One responsibility: pick a provider from config. Kept separate from both providers so neither imports the other.
- `tests/core/memory/test_openai_embedding_provider.py`
- `tests/core/memory/test_embedding_backend.py`

**Modify:**
- `shared/config.py` — `BAAI/bge-m3` dim, `embedding_backend`, `embedding_host`.
- `core/conscious/__main__.py:175`, `core/channels/admin_api.py:96-105`, `core/memory/ingestor_main.py:43-45`, `core/librarian/__main__.py:16,42` — construct via the factory.
- `core/memory/redis_vector_store.py` — dimension guard in `ensure_index()`.
- `core/memory/sqlite_vec_store.py` — dimension guard in `_ensure_schema()`.
- `runner/__main__.py:30-36` — add `EMBEDDING_HOST` to `_GATEWAY_REWRITE_KEYS`.
- `alfredctl/doctor.py:140-151` — report the backend.
- `.env.example`, `docs/architecture.md`, `docs/PRD.md`, `CLAUDE.md`.

---

### Task 1: Config — backend selection and the bge-m3 dimension

**Files:**
- Modify: `shared/config.py:45-51` (dims table), `shared/config.py:105-107` (fields), `shared/config.py:154-157` + `shared/config.py:186-188` (`from_env`)
- Test: `tests/shared/test_embedding_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/shared/test_embedding_config.py`:

```python
def test_bge_m3_dimension_is_known() -> None:
    from shared.config import embedding_dim_for

    assert embedding_dim_for("BAAI/bge-m3") == 1024


def test_embedding_backend_defaults_to_sentence_transformers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from shared.config import AlfredConfig

    monkeypatch.delenv("EMBEDDING_BACKEND", raising=False)
    monkeypatch.delenv("EMBEDDING_HOST", raising=False)
    config = AlfredConfig.from_env()
    assert config.embedding_backend == "sentence_transformers"
    assert config.embedding_host == "http://localhost:8001"


def test_embedding_backend_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from shared.config import AlfredConfig

    monkeypatch.setenv("EMBEDDING_BACKEND", "OpenAI")
    monkeypatch.setenv("EMBEDDING_HOST", "http://vllm:8001/")
    config = AlfredConfig.from_env()
    # Normalised: lowercased, and no trailing slash (the client appends /v1/...).
    assert config.embedding_backend == "openai"
    assert config.embedding_host == "http://vllm:8001"


def test_embedding_dim_tracks_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from shared.config import AlfredConfig

    monkeypatch.setenv("EMBEDDING_MODEL", "BAAI/bge-m3")
    monkeypatch.delenv("EMBEDDING_DIM", raising=False)
    assert AlfredConfig.from_env().embedding_dim == 1024
```

If `pytest` and `import pytest` are not already imported at the top of that file, add them.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/shared/test_embedding_config.py -v`
Expected: FAIL — `AttributeError: 'AlfredConfig' object has no attribute 'embedding_backend'` and `assert 384 == 1024`.

- [ ] **Step 3: Add the dimension entry**

In `shared/config.py`, inside `_KNOWN_EMBEDDING_DIMS`, add after the `BAAI/bge-base-en-v1.5` line:

```python
    "BAAI/bge-m3": 1024,
```

- [ ] **Step 4: Add the config fields**

In `shared/config.py`, replace the `# Memory: Embedding` block:

```python
    # Memory: Embedding
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    embedding_dim: int = 384
```

with:

```python
    # Memory: Embedding. ``embedding_backend`` picks how the model is run, not
    # which model: ``sentence_transformers`` loads it in-process (default — a
    # fresh clone needs no server), ``openai`` calls an OpenAI-compatible
    # /v1/embeddings server (vLLM --runner pooling) at ``embedding_host``.
    # ``embedding_model`` names the model either way, so ``embedding_dim``
    # keeps tracking it through ``embedding_dim_for()``.
    embedding_backend: str = "sentence_transformers"
    embedding_host: str = "http://localhost:8001"
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    embedding_dim: int = 384
```

- [ ] **Step 5: Wire from_env**

In `shared/config.py`, in `from_env`, immediately after the two existing `embedding_model = ...` / `embedding_dim = ...` lines, add:

```python
        # Host has no /v1 suffix — the client appends the path, matching
        # OPENAI_COMPAT_HOST. Strip a trailing slash so we never build "//v1".
        embedding_host = os.getenv("EMBEDDING_HOST", "http://localhost:8001").strip().rstrip("/")
        embedding_backend = (
            os.getenv("EMBEDDING_BACKEND", "sentence_transformers").strip().lower()
            or "sentence_transformers"
        )
```

Then in the `return cls(...)` call, replace:

```python
            # Memory: Embedding (env-configurable; see above for the dim default).
            embedding_model=embedding_model,
            embedding_dim=embedding_dim,
```

with:

```python
            # Memory: Embedding (env-configurable; see above for the dim default).
            embedding_backend=embedding_backend,
            embedding_host=embedding_host,
            embedding_model=embedding_model,
            embedding_dim=embedding_dim,
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/shared/test_embedding_config.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 7: Lint, type-check, commit**

```bash
ruff check . --fix && ruff format .
mypy --strict shared/
git add shared/config.py tests/shared/test_embedding_config.py
git commit -m "feat(config): add EMBEDDING_BACKEND/EMBEDDING_HOST and bge-m3 dimension"
```

---

### Task 2: The OpenAI-compatible embedding provider

**Files:**
- Create: `core/memory/openai_embedding_provider.py`
- Test: `tests/core/memory/test_openai_embedding_provider.py`

Three behaviours are easy to get wrong and are each pinned by a test below:
1. **Order.** The OpenAI embeddings response carries an `index` per item and is not
   guaranteed to arrive in request order. Sort by `index` or embeddings silently attach
   to the wrong text — a corruption with no error and no log.
2. **Empty input.** vLLM rejects an empty `input` array with HTTP 400. Return `[]` without
   a request.
3. **Declared dimension.** `dimension()` is sync in the ABC and must not do I/O. It
   returns the configured dim; `warmup()` is where a server/config mismatch is caught.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/memory/test_openai_embedding_provider.py`:

```python
from __future__ import annotations

import httpx
import pytest

from core.memory.openai_embedding_provider import OpenAICompatEmbeddingProvider


def _provider(
    handler: object,
    *,
    dim: int = 4,
    model: str = "BAAI/bge-m3",
) -> OpenAICompatEmbeddingProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return OpenAICompatEmbeddingProvider(
        model_name=model, host="http://embed:8001", dim=dim, client=client
    )


@pytest.mark.asyncio
async def test_embed_returns_the_vector() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        return httpx.Response(
            200, json={"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3, 0.4]}]}
        )

    provider = _provider(handler)
    assert await provider.embed("hello") == [0.1, 0.2, 0.3, 0.4]


@pytest.mark.asyncio
async def test_embed_sends_model_and_input() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 4}]})

    provider = _provider(handler)
    await provider.embed("hello")
    assert seen["model"] == "BAAI/bge-m3"
    assert seen["input"] == ["hello"]


@pytest.mark.asyncio
async def test_embed_batch_reorders_by_index() -> None:
    """The API may return items out of order — index, not position, is authoritative."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 2, "embedding": [2.0] * 4},
                    {"index": 0, "embedding": [0.0] * 4},
                    {"index": 1, "embedding": [1.0] * 4},
                ]
            },
        )

    provider = _provider(handler)
    results = await provider.embed_batch(["a", "b", "c"])
    assert [r[0] for r in results] == [0.0, 1.0, 2.0]


@pytest.mark.asyncio
async def test_embed_batch_empty_makes_no_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("empty input must not hit the server")

    provider = _provider(handler)
    assert await provider.embed_batch([]) == []


@pytest.mark.asyncio
async def test_embed_raises_on_short_response() -> None:
    """A truncated batch response must fail loudly, not return fewer vectors."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 4}]})

    provider = _provider(handler)
    with pytest.raises(RuntimeError, match="returned 1 embedding"):
        await provider.embed_batch(["a", "b"])


@pytest.mark.asyncio
async def test_embed_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "unavailable"})

    provider = _provider(handler)
    with pytest.raises(httpx.HTTPStatusError):
        await provider.embed("hello")


def test_dimension_and_model_name_need_no_network() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("metadata accessors must not make requests")

    provider = _provider(handler, dim=1024)
    assert provider.dimension() == 1024
    assert provider.model_name() == "BAAI/bge-m3"


@pytest.mark.asyncio
async def test_warmup_rejects_a_dimension_mismatch() -> None:
    """Configured dim must match what the server actually returns."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 8}]})

    provider = _provider(handler, dim=4)
    with pytest.raises(RuntimeError, match="EMBEDDING_DIM"):
        await provider.warmup()


@pytest.mark.asyncio
async def test_warmup_passes_when_dimensions_agree() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 4}]})

    provider = _provider(handler, dim=4)
    await provider.warmup()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/memory/test_openai_embedding_provider.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.memory.openai_embedding_provider'`.

- [ ] **Step 3: Write the implementation**

Create `core/memory/openai_embedding_provider.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/memory/test_openai_embedding_provider.py -v`
Expected: PASS — 9 passed.

- [ ] **Step 5: Lint, type-check, commit**

```bash
ruff check . --fix && ruff format .
mypy --strict core/
git add core/memory/openai_embedding_provider.py tests/core/memory/test_openai_embedding_provider.py
git commit -m "feat(memory): add OpenAI-compatible embedding provider"
```

---

### Task 3: The backend factory

**Files:**
- Create: `core/memory/embedding_backend.py`
- Test: `tests/core/memory/test_embedding_backend.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/core/memory/test_embedding_backend.py`:

```python
from __future__ import annotations

import dataclasses

import pytest

from core.memory.embedding_backend import build_embedding_provider
from shared.config import AlfredConfig


def _config(**overrides: object) -> AlfredConfig:
    return dataclasses.replace(AlfredConfig(), **overrides)  # type: ignore[arg-type]


def test_openai_backend_builds_the_http_provider() -> None:
    from core.memory.openai_embedding_provider import OpenAICompatEmbeddingProvider

    provider = build_embedding_provider(
        _config(
            embedding_backend="openai",
            embedding_host="http://embed:8001",
            embedding_model="BAAI/bge-m3",
            embedding_dim=1024,
        )
    )
    assert isinstance(provider, OpenAICompatEmbeddingProvider)
    assert provider.model_name() == "BAAI/bge-m3"
    assert provider.dimension() == 1024


def test_default_backend_builds_sentence_transformers() -> None:
    from core.memory.embedding_provider import SentenceTransformerProvider

    provider = build_embedding_provider(_config(embedding_model="all-MiniLM-L6-v2"))
    assert isinstance(provider, SentenceTransformerProvider)
    assert provider.model_name() == "all-MiniLM-L6-v2"


def test_unknown_backend_fails_loudly() -> None:
    with pytest.raises(RuntimeError, match="Unknown EMBEDDING_BACKEND"):
        build_embedding_provider(_config(embedding_backend="banana"))
```

Note: `AlfredConfig` is `@dataclass(frozen=True)` (`shared/config.py:74`), so
`dataclasses.replace` gives a config carrying the declared defaults with no env reads —
which is what keeps these tests independent of the developer's shell.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/memory/test_embedding_backend.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.memory.embedding_backend'`.

- [ ] **Step 3: Write the implementation**

Create `core/memory/embedding_backend.py`:

```python
"""Embedding backend dispatcher (env ``EMBEDDING_BACKEND``: sentence_transformers | openai).

``sentence_transformers`` (default) loads the model in-process, so a fresh clone works
with nothing else running. ``openai`` talks to any OpenAI-compatible ``/v1/embeddings``
server (vLLM ``--runner pooling``) at ``EMBEDDING_HOST``, which collapses one resident
model per service down to one shared server.

The memory counterpart of :mod:`core.reflex.inference`. Every service builds its
provider here rather than naming a concrete class, so the backend is one env var.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.memory.embedding_provider import EmbeddingProvider
    from shared.config import AlfredConfig

_BACKENDS = ("sentence_transformers", "openai")


def build_embedding_provider(config: AlfredConfig) -> EmbeddingProvider:
    """Construct the embedding provider named by ``config.embedding_backend``."""
    backend = config.embedding_backend.strip().lower() or "sentence_transformers"
    if backend not in _BACKENDS:
        raise RuntimeError(
            f"Unknown EMBEDDING_BACKEND {backend!r} (expected one of: {', '.join(_BACKENDS)})"
        )
    if backend == "openai":
        # Imported lazily so the sentence-transformers path never pays for httpx
        # setup, and vice versa — the ST import pulls in torch.
        from core.memory.openai_embedding_provider import OpenAICompatEmbeddingProvider

        return OpenAICompatEmbeddingProvider(
            model_name=config.embedding_model,
            host=config.embedding_host,
            dim=config.embedding_dim,
        )

    from core.memory.embedding_provider import SentenceTransformerProvider

    return SentenceTransformerProvider(config.embedding_model)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/memory/test_embedding_backend.py -v`
Expected: PASS — 3 passed.

- [ ] **Step 5: Lint, type-check, commit**

```bash
ruff check . --fix && ruff format .
mypy --strict core/
git add core/memory/embedding_backend.py tests/core/memory/test_embedding_backend.py
git commit -m "feat(memory): add EMBEDDING_BACKEND provider factory"
```

---

### Task 4: Route every construction site through the factory

**Files:**
- Modify: `core/conscious/__main__.py:28,175`
- Modify: `core/channels/admin_api.py:96,105`
- Modify: `core/memory/ingestor_main.py:43,45`
- Modify: `core/librarian/__main__.py:16,42`
- Test: `tests/core/memory/test_embedding_backend.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/core/memory/test_embedding_backend.py`:

```python
def test_no_service_constructs_a_provider_directly() -> None:
    """Services must go through the factory, or EMBEDDING_BACKEND is a lie in that process."""
    import pathlib

    entry_points = [
        "core/conscious/__main__.py",
        "core/channels/admin_api.py",
        "core/memory/ingestor_main.py",
        "core/librarian/__main__.py",
    ]
    offenders = [
        path
        for path in entry_points
        if "SentenceTransformerProvider(" in pathlib.Path(path).read_text()
    ]
    assert offenders == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/memory/test_embedding_backend.py::test_no_service_constructs_a_provider_directly -v`
Expected: FAIL — `assert [...4 paths...] == []`.

- [ ] **Step 3: Update the conscious engine**

In `core/conscious/__main__.py`, replace the import at line 28:

```python
from core.memory.embedding_provider import SentenceTransformerProvider
```

with:

```python
from core.memory.embedding_backend import build_embedding_provider
```

and at line 175 replace:

```python
        embedder = SentenceTransformerProvider(config.embedding_model)
```

with:

```python
        embedder = build_embedding_provider(config)
```

- [ ] **Step 4: Update the admin API**

In `core/channels/admin_api.py`, replace the lazy import at line 96:

```python
            from core.memory.embedding_provider import SentenceTransformerProvider
```

with:

```python
            from core.memory.embedding_backend import build_embedding_provider
```

and at line 105 replace:

```python
                embedder=SentenceTransformerProvider(config.embedding_model),
```

with:

```python
                embedder=build_embedding_provider(config),
```

- [ ] **Step 5: Update the memory ingestor**

In `core/memory/ingestor_main.py`, replace the import at line 43:

```python
    from core.memory.embedding_provider import SentenceTransformerProvider
```

with:

```python
    from core.memory.embedding_backend import build_embedding_provider
```

and at line 45 replace:

```python
    embedder = SentenceTransformerProvider(config.embedding_model)
```

with:

```python
    embedder = build_embedding_provider(config)
```

- [ ] **Step 6: Update the librarian**

In `core/librarian/__main__.py`, replace the import at line 16:

```python
from core.memory.embedding_provider import SentenceTransformerProvider
```

with:

```python
from core.memory.embedding_backend import build_embedding_provider
```

and at line 42 replace:

```python
        embedder = SentenceTransformerProvider(config.embedding_model)
```

with:

```python
        embedder = build_embedding_provider(config)
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `uv run pytest tests/core/memory/test_embedding_backend.py -v`
Expected: PASS — 4 passed.

- [ ] **Step 8: Verify nothing else regressed**

Run: `uv run pytest tests/core/ tests/shared/ -q`
Expected: PASS, except the four pre-existing CUDA out-of-memory failures in
`tests/core/memory/test_embedding_provider.py` (they load a real model onto the GPU and
fail whenever the box's vLLM containers hold the VRAM — unrelated to this branch).

- [ ] **Step 9: Lint, type-check, commit**

```bash
ruff check . --fix && ruff format .
mypy --strict core/
git add core/conscious/__main__.py core/channels/admin_api.py core/memory/ingestor_main.py core/librarian/__main__.py tests/core/memory/test_embedding_backend.py
git commit -m "refactor(memory): build embedding providers through the backend factory"
```

---

### Task 5: Dimension guard for the hot vector store

**Files:**
- Modify: `core/memory/redis_vector_store.py:47-115`
- Test: `tests/core/memory/test_redis_vector_store.py`

Today `ensure_index()` catches `"Index already exists"` and sets `_index_ready = True`
without ever comparing dimensions. Change the embedding model — which is exactly what
this branch makes easy — and the store writes 1024-dim vectors into a 768-dim index. Per
`CLAUDE.md` this is the established silent-failure shape: no exception, no log, recall
just returns nothing. Fail loudly instead.

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/memory/test_redis_vector_store.py`:

```python
@pytest.mark.asyncio
async def test_ensure_index_rejects_a_dimension_mismatch() -> None:
    """An existing index at another dim must fail loudly, not silently mismatch."""
    from core.memory.redis_vector_store import RedisVectorStore

    class ExistingIndexRedis:
        async def execute_command(self, *args: object) -> object:
            if args[0] == "FT.CREATE":
                raise RuntimeError("Index already exists")
            if args[0] == "FT.INFO":
                return {"attributes": [{"attribute": "embedding_content", "dim": "768"}]}
            raise AssertionError(f"unexpected command {args[0]!r}")

    store = RedisVectorStore(ExistingIndexRedis(), dim=1024)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="FT.DROPINDEX"):
        await store.ensure_index()


@pytest.mark.asyncio
async def test_ensure_index_accepts_a_matching_dimension() -> None:
    from core.memory.redis_vector_store import RedisVectorStore

    class MatchingIndexRedis:
        async def execute_command(self, *args: object) -> object:
            if args[0] == "FT.CREATE":
                raise RuntimeError("Index already exists")
            if args[0] == "FT.INFO":
                return {"attributes": [{"attribute": "embedding_content", "dim": "1024"}]}
            raise AssertionError(f"unexpected command {args[0]!r}")

    store = MatchingIndexRedis()
    vector_store = RedisVectorStore(store, dim=1024)  # type: ignore[arg-type]
    await vector_store.ensure_index()


@pytest.mark.asyncio
async def test_ensure_index_tolerates_an_unreadable_dimension() -> None:
    """If FT.INFO cannot be parsed we proceed — a parse quirk must not take memory down."""
    from core.memory.redis_vector_store import RedisVectorStore

    class OpaqueIndexRedis:
        async def execute_command(self, *args: object) -> object:
            if args[0] == "FT.CREATE":
                raise RuntimeError("Index already exists")
            if args[0] == "FT.INFO":
                return {"attributes": []}
            raise AssertionError(f"unexpected command {args[0]!r}")

    vector_store = RedisVectorStore(OpaqueIndexRedis(), dim=1024)  # type: ignore[arg-type]
    await vector_store.ensure_index()
```

If `pytest` is not already imported in that file, add `import pytest`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/memory/test_redis_vector_store.py -k dimension -v`
Expected: FAIL — `DID NOT RAISE <class 'RuntimeError'>` on the first test.

- [ ] **Step 3: Add the dimension parser**

In `core/memory/redis_vector_store.py`, in the Helpers section next to `_parse_ft_info`,
add:

```python
def _index_vector_dim(info: dict[str, object]) -> int | None:
    """Vector dimension declared by an existing index, or None if undeterminable.

    ``FT.INFO``'s ``attributes`` entry is a list of per-field descriptors whose shape
    follows the negotiated protocol: RESP3 gives dicts, RESP2 gives flat alternating
    lists. Normalise both, then read ``dim`` off the first vector field. Returning
    None (rather than guessing) keeps a parse quirk from failing startup.
    """
    attributes = info.get("attributes")
    if not isinstance(attributes, (list, tuple)):
        return None
    for attribute in attributes:
        if isinstance(attribute, dict):
            fields = {_decoded(k): v for k, v in attribute.items()}
        elif isinstance(attribute, (list, tuple)):
            items = list(attribute)
            fields = {_decoded(items[i]): items[i + 1] for i in range(0, len(items) - 1, 2)}
        else:
            continue
        raw_dim = fields.get("dim")
        if isinstance(raw_dim, (bytes, str, int)):
            try:
                return int(raw_dim)
            except ValueError:
                return None
    return None
```

- [ ] **Step 4: Check the dimension when the index already exists**

In `core/memory/redis_vector_store.py`, in `ensure_index()`, replace:

```python
            if "Index already exists" in err or "already exists" in err.lower():
                self._index_ready = True
                logger.debug("RediSearch index %s already exists", CONTEXT_INDEX)
```

with:

```python
            if "Index already exists" in err or "already exists" in err.lower():
                await self._verify_index_dim()
                self._index_ready = True
                logger.debug("RediSearch index %s already exists", CONTEXT_INDEX)
```

and add this method immediately after `ensure_index()`:

```python
    async def _verify_index_dim(self) -> None:
        """Fail loudly if the existing index was built at a different dimension.

        Changing EMBEDDING_MODEL (or EMBEDDING_BACKEND) changes the vector width.
        Writing those vectors into an index built at the old width produces no
        exception and no log — searches simply stop matching. Refuse to start.
        """
        raw = await self._redis.execute_command("FT.INFO", CONTEXT_INDEX)  # type: ignore[no-untyped-call]
        existing = _index_vector_dim(_parse_ft_info(raw))
        if existing is None or existing == self._dim:
            return
        raise RuntimeError(
            f"RediSearch index {CONTEXT_INDEX} was built with dim={existing} but the "
            f"configured embedding model produces dim={self._dim}. Vector search would "
            f"silently return nothing. Re-embed into a fresh index "
            f"(FT.DROPINDEX {CONTEXT_INDEX} DD, then restart), or restore the previous "
            f"EMBEDDING_MODEL/EMBEDDING_BACKEND."
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/core/memory/test_redis_vector_store.py -v`
Expected: PASS (whole file, including the three new tests).

- [ ] **Step 6: Lint, type-check, commit**

```bash
ruff check . --fix && ruff format .
mypy --strict core/
git add core/memory/redis_vector_store.py tests/core/memory/test_redis_vector_store.py
git commit -m "fix(memory): reject a RediSearch index built at a different dimension"
```

---

### Task 6: Dimension guard for the cold vector store

**Files:**
- Modify: `core/memory/sqlite_vec_store.py:99-143`
- Test: `tests/core/memory/test_sqlite_vec_store.py`

`_migrate_v2` creates the vec0 tables with `CREATE VIRTUAL TABLE IF NOT EXISTS
vec0(embedding float[{dim}])`. On an already-migrated database that DDL never runs again,
so a dimension change leaves the old width in place and every insert fails at runtime.
Detect it at schema time with the same actionable error as Task 5.

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/memory/test_sqlite_vec_store.py`:

```python
@pytest.mark.asyncio
async def test_schema_rejects_a_dimension_mismatch(tmp_path: Path) -> None:
    """An existing vec0 table at another width must fail loudly at schema time."""
    from core.memory.sqlite_vec_store import SqliteVecStore

    db_path = tmp_path / "cold.db"
    store = SqliteVecStore(str(db_path), dim=384)
    await store._ensure_schema()
    await store.close()

    reopened = SqliteVecStore(str(db_path), dim=1024)
    try:
        if not reopened._vec_ready:
            pytest.skip("sqlite-vec extension unavailable — nothing to guard")
        with pytest.raises(RuntimeError, match="dim=384"):
            await reopened._ensure_schema()
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_schema_accepts_a_matching_dimension(tmp_path: Path) -> None:
    from core.memory.sqlite_vec_store import SqliteVecStore

    db_path = tmp_path / "cold.db"
    store = SqliteVecStore(str(db_path), dim=384)
    await store._ensure_schema()
    await store.close()

    reopened = SqliteVecStore(str(db_path), dim=384)
    try:
        await reopened._ensure_schema()
    finally:
        await reopened.close()
```

The signatures are confirmed: `SqliteVecStore(db_path, dim=384, embedder=None)`
(`core/memory/sqlite_vec_store.py:54-58`) and `async def close()`
(`core/memory/sqlite_vec_store.py:418`). Add `import pytest` and `from pathlib import Path`
to the test file if they are not already there.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/memory/test_sqlite_vec_store.py -k dimension -v`
Expected: FAIL — `DID NOT RAISE <class 'RuntimeError'>` (or SKIP if sqlite-vec is not
installed, in which case install the `memory` extra and re-run: `uv sync --all-extras`).

- [ ] **Step 3: Add the guard**

In `core/memory/sqlite_vec_store.py`, add this method to the class, immediately after
`_ensure_schema`:

```python
    async def _verify_vec_dim(self, db: aiosqlite.Connection) -> None:
        """Fail loudly if the vec0 tables were created at a different dimension.

        ``CREATE VIRTUAL TABLE IF NOT EXISTS`` is a no-op on an existing table, so a
        changed EMBEDDING_MODEL/EMBEDDING_BACKEND leaves the old width in place and
        every insert fails later, far from the cause. The declared width is recoverable
        from the stored DDL.
        """
        if not self._vec_ready:
            return
        cursor = await db.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("vec_episodic_content",),
        )
        row = await cursor.fetchone()
        if row is None or not row[0]:
            return  # table not created yet — _migrate_v2 will build it at self._dim
        match = re.search(r"float\[(\d+)\]", row[0])
        if match is None:
            return
        existing = int(match.group(1))
        if existing == self._dim:
            return
        raise RuntimeError(
            f"Cold store vec0 tables were built with dim={existing} but the configured "
            f"embedding model produces dim={self._dim}. Re-embed into a fresh cold store "
            f"(delete the sqlite file and restart), or restore the previous "
            f"EMBEDDING_MODEL/EMBEDDING_BACKEND."
        )
```

Add `import re` to the module's imports — `core/memory/sqlite_vec_store.py` does not
currently import it.

- [ ] **Step 4: Call the guard from both schema paths**

In `_ensure_schema`, the fast path returns early on an already-migrated database, so the
check must run there too. Replace:

```python
            if row is not None and row[0] == 1 and row[1] is not None and row[1] >= 2:
                self._schema_ready = True
                return
```

with:

```python
            if row is not None and row[0] == 1 and row[1] is not None and row[1] >= 2:
                await self._verify_vec_dim(db)
                self._schema_ready = True
                return
```

and replace the end of the method:

```python
        self._schema_ready = True
```

with:

```python
        await self._verify_vec_dim(db)
        self._schema_ready = True
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/core/memory/test_sqlite_vec_store.py -v`
Expected: PASS (whole file, including the two new tests).

- [ ] **Step 6: Lint, type-check, commit**

```bash
ruff check . --fix && ruff format .
mypy --strict core/
git add core/memory/sqlite_vec_store.py tests/core/memory/test_sqlite_vec_store.py
git commit -m "fix(memory): reject a cold store built at a different dimension"
```

---

### Task 7: Environment plumbing — container gateway, .env.example, doctor

**Files:**
- Modify: `runner/__main__.py:30-36`
- Modify: `.env.example:33-37`
- Modify: `alfredctl/doctor.py:140-151`
- Test: `tests/runner/test_gateway_rewrite.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/runner/test_gateway_rewrite.py`, matching the monkeypatch style already
used there (string target, not a module object):

```python
def test_rewrites_embedding_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """In-container, EMBEDDING_HOST=localhost means the container, not the box."""
    monkeypatch.setattr("runner.__main__._reachable_gateway", lambda: "host.docker.internal")
    env = {
        "ALFRED_MANAGE_INFRA": "1",
        "EMBEDDING_HOST": "http://localhost:8001",
    }
    rewrite_host_gateway(env)
    assert env["EMBEDDING_HOST"] == "http://host.docker.internal:8001"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/runner/test_gateway_rewrite.py -k embedding -v`
Expected: FAIL — `assert 'http://localhost:8001' == 'http://host.docker.internal:8001'`.

- [ ] **Step 3: Add the key**

In `runner/__main__.py`, in `_GATEWAY_REWRITE_KEYS`, add after `"OPENAI_COMPAT_HOST",`:

```python
    "EMBEDDING_HOST",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/runner/test_gateway_rewrite.py -k embedding -v`
Expected: PASS.

- [ ] **Step 5: Document the vars in .env.example**

In `.env.example`, replace the existing embedding block (the comment plus
`EMBEDDING_MODEL=` and `EMBEDDING_DIM=`) with:

```bash
# Memory embeddings.
# EMBEDDING_BACKEND selects how the model runs, not which model:
#   sentence_transformers (default) — loads in-process; no server needed.
#   openai                          — calls an OpenAI-compatible /v1/embeddings
#                                     server (e.g. vLLM started with --runner pooling).
# EMBEDDING_DIM auto-tracks known models (set it only for an unknown model).
# Changing the model changes the vector index width — the stores refuse to start
# against an index built at a different dimension rather than silently mismatch.
EMBEDDING_BACKEND=sentence_transformers
# For EMBEDDING_BACKEND=openai (no /v1 suffix; the client appends it):
EMBEDDING_HOST=http://localhost:8001
EMBEDDING_MODEL=
EMBEDDING_DIM=
```

- [ ] **Step 6: Report the backend in doctor**

In `alfredctl/doctor.py`, replace the whole body of `_check_embeddings` (lines 138-150):

```python
def _check_embeddings(env: dict[str, str]) -> DoctorCheck:
    from shared.config import DEFAULT_EMBEDDING_MODEL

    model = env.get("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL).strip()
    backend = env.get("EMBEDDING_BACKEND", "sentence_transformers").strip().lower()

    if backend == "openai":
        host = env.get("EMBEDDING_HOST", "").strip()
        if not host:
            return DoctorCheck(
                "memory embeddings",
                "fail",
                "EMBEDDING_BACKEND=openai requires EMBEDDING_HOST",
            )
        # Gating is irrelevant on this path — the server holds the weights, not us.
        return DoctorCheck("memory embeddings", "pass", f"model={model} via {host}")

    gated = model.startswith("google/embeddinggemma")
    if gated and not env.get("HF_TOKEN", "").strip():
        return DoctorCheck(
            "memory embeddings",
            "warn",
            f"{model} is gated — set HF_TOKEN + accept its license, or use the ungated default",
        )
    return DoctorCheck("memory embeddings", "pass", f"model={model}")
```

The gated-model warning now sits below the `openai` branch on purpose: with a remote
server nothing is downloaded locally, so an HF token is not required and warning about
one would be wrong.

- [ ] **Step 7: Verify doctor still runs**

Run: `uv run alfredctl doctor`
Expected: exits without traceback; the `memory embeddings` line reads `pass`.

- [ ] **Step 8: Lint, type-check, commit**

```bash
ruff check . --fix && ruff format .
mypy --strict alfredctl/ runner/
git add runner/__main__.py .env.example alfredctl/doctor.py tests/runner/test_gateway_rewrite.py
git commit -m "feat(config): plumb EMBEDDING_HOST through the container gateway and doctor"
```

---

### Task 8: Documentation

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/PRD.md`
- Modify: `CLAUDE.md`
- Modify: `core/CLAUDE.md`

- [ ] **Step 1: Document the backend seam in architecture.md**

`docs/architecture.md:442` currently states embeddings are "computed via
`sentence-transformers`", which stops being unconditionally true. Replace that sentence:

```markdown
Two-tier storage: Redis for hot (recent) entries, SQLite for cold archive. Entries are `EpisodicEntry` models with timestamps, source, content, and importance scores. Embeddings are computed via the configured embedding backend (see below) for semantic search. A `DecayScheduler` handles time-based importance decay.
```

Then add this subsection at the end of §3.7, immediately before `### 3.8`:

```markdown
#### Embedding backends

`EmbeddingProvider` (`core/memory/embedding_provider.py`) has two implementations, selected
per-process by `EMBEDDING_BACKEND` through `build_embedding_provider()`
(`core/memory/embedding_backend.py`) — the memory counterpart of the `REFLEX_BACKEND` seam:

| Backend | Class | Where the model lives |
|---|---|---|
| `sentence_transformers` (default) | `SentenceTransformerProvider` | In-process, one copy per service |
| `openai` | `OpenAICompatEmbeddingProvider` | A shared OpenAI-compatible `/v1/embeddings` server at `EMBEDDING_HOST` |

Four services construct a provider (conscious, channels/admin, memory ingestor, librarian).
Under the default backend each loads its own copy of the model and of torch; the `openai`
backend collapses that onto one resident model. vLLM serves embeddings when started with
`--runner pooling`.

`EMBEDDING_MODEL` names the model under either backend, so `EMBEDDING_DIM` keeps tracking it
via `embedding_dim_for()`. Both vector stores refuse to start against an index built at a
different dimension — see the gotcha in `CLAUDE.md`.
```

- [ ] **Step 2: Update the PRD Capability Catalog**

In `docs/PRD.md`, add a row to the §4.3 Memory table (line 96), after the significance
scoring row:

```markdown
| Embeddings run in-process or against a shared inference server (one resident model, not one per service) | Shipped | plan `2026-09-03-vllm-embedding-adapter.md` |
```

Then bump line 3 from `**2026-07-24**` to `**2026-09-03**`.

Note: if PR #192 (passive observation) merged before this branch, it already bumped that
date and added its own §4.3 row — rebase first and keep both rows rather than reverting
its edit.

- [ ] **Step 3: Add the dimension gotcha to CLAUDE.md**

In `CLAUDE.md`, in the Gotchas list, find the bullet beginning "Default embedding model is
ungated". Immediately after it, add:

```markdown
- `EMBEDDING_BACKEND` selects how the embedding model runs, not which one:
  `sentence_transformers` (default) loads it in-process — one copy per service, and torch
  with it — while `openai` calls a shared OpenAI-compatible `/v1/embeddings` server at
  `EMBEDDING_HOST` (vLLM needs `--runner pooling`). Build providers via
  `build_embedding_provider()` (`core/memory/embedding_backend.py`), never by naming
  `SentenceTransformerProvider` in a service. **Changing the model changes the vector
  width**, and both stores used to accept that silently: `FT.CREATE` returns "Index already
  exists" and `CREATE VIRTUAL TABLE IF NOT EXISTS` is a no-op, so vectors of the new width
  went into an index of the old one and recall returned nothing with no exception and no
  log. Both now compare dimensions at startup and refuse to run — re-embed into a fresh
  index (`FT.DROPINDEX idx:context DD` + delete the cold sqlite file) when you change models.
```

- [ ] **Step 4: Update core/CLAUDE.md**

In `core/CLAUDE.md`, under the Memory section, replace the `embedding_provider.py` bullet:

```markdown
- `embedding_provider.py` — EmbeddingProvider ABC + SentenceTransformer (lazy-loaded, async via to_thread)
```

with:

```markdown
- `embedding_provider.py` — EmbeddingProvider ABC + SentenceTransformer (lazy-loaded, async via to_thread)
- `openai_embedding_provider.py` — `OpenAICompatEmbeddingProvider`: `/v1/embeddings` over HTTP (vLLM `--runner pooling`)
- `embedding_backend.py` — `build_embedding_provider(config)`: the `EMBEDDING_BACKEND` seam; services call this, never a concrete provider
```

- [ ] **Step 5: Commit**

```bash
git add docs/architecture.md docs/PRD.md CLAUDE.md core/CLAUDE.md
git commit -m "docs: document the embedding backend seam and dimension guards"
```

---

## Final verification

- [ ] **Full Python suite**

Run: `uv sync --all-extras && uv run pytest -q`
Expected: all pass except the four pre-existing CUDA out-of-memory failures in
`tests/core/memory/test_embedding_provider.py`. Those load a real model onto the GPU and
fail whenever the box's vLLM containers hold the VRAM. Confirm the count is exactly four
and that each is an `AcceleratorError` — any other failure belongs to this branch.

- [ ] **Lint and types**

```bash
ruff check . && ruff format --check .
mypy --strict alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/
```
Expected: ruff clean; mypy `Success: no issues found`.

- [ ] **Live smoke against the real server** (this box only; skip elsewhere)

```bash
EMBEDDING_BACKEND=openai EMBEDDING_HOST=http://localhost:8001 EMBEDDING_MODEL=BAAI/bge-m3 \
  uv run python -c "
import asyncio
from core.memory.embedding_backend import build_embedding_provider
from shared.config import AlfredConfig

async def main() -> None:
    provider = build_embedding_provider(AlfredConfig.from_env())
    await provider.warmup()
    vectors = await provider.embed_batch(['the kitchen light turned on', 'hello'])
    print(len(vectors), len(vectors[0]))

asyncio.run(main())
"
```
Expected: prints `2 1024` after a `Embedding backend ready: BAAI/bge-m3 ... (dim=1024)` log line.

- [ ] **Push and open the PR**

```bash
git push -u origin feat/vllm-embedding-adapter
gh pr create --title "feat(memory): add an OpenAI-compatible embedding backend" --body "$(cat <<'BODY'
## What

Adds a second `EmbeddingProvider` implementation that calls an OpenAI-compatible
`/v1/embeddings` server, selected by `EMBEDDING_BACKEND` through a new
`build_embedding_provider()` factory — the memory counterpart of the existing
`REFLEX_BACKEND` seam.

## Why

Four services (conscious, channels/admin, memory ingestor, librarian) each construct
their own provider, so the default backend loads one copy of the embedding model — and
of torch — per process. Pointing them at a shared server collapses that to a single
resident model. This is the "externalize embedding" alternative anticipated in
`docs/backlog/medium/cpu-only-torch-index.md`, and it gives
`docs/backlog/high/embedding-model-gated-first-run.md` a path that needs no HF token.

## Notes

- `sentence_transformers` remains the **default** backend, so a fresh clone still works
  with no embedding server running. Nothing changes unless `EMBEDDING_BACKEND=openai`.
- Switching backends changes the embedding model and therefore the vector width. Both
  vector stores previously accepted that silently — `FT.CREATE` returns "Index already
  exists" and `CREATE VIRTUAL TABLE IF NOT EXISTS` is a no-op — so vectors of the new
  width went into an index of the old one and recall returned nothing with no exception
  and no log. Both now compare dimensions at startup and refuse to run.
- Enabling this on the deployment box is an `~/code/alfred-deploy/.env` change, which no
  workflow writes and this PR does not touch.
BODY
)"
```

Do **not** merge. Merging to `master` triggers the `deploy to lath-server` job, which
rebuilds and restarts the live stack — that is the user's call, not the implementer's.

## Deployment note (not part of this branch)

Turning the backend on for the running deployment is an `~/code/alfred-deploy/.env` change,
which no workflow writes and this PR must not touch:

```bash
EMBEDDING_BACKEND=openai
EMBEDDING_HOST=http://host.docker.internal:8001
EMBEDDING_MODEL=BAAI/bge-m3
```

`idx:context` currently holds zero documents, so no re-embedding is needed today; if that
changes before this is switched on, drop and rebuild the index first or the Task 5 guard
will (correctly) refuse to start.
