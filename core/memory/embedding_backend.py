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

from shared.config import DEFAULT_EMBEDDING_BACKEND

if TYPE_CHECKING:
    from collections.abc import Callable

    from core.memory.embedding_provider import EmbeddingProvider
    from shared.config import AlfredConfig


def _build_sentence_transformers(config: AlfredConfig) -> EmbeddingProvider:
    from core.memory.embedding_provider import SentenceTransformerProvider

    return SentenceTransformerProvider(config.embedding_model)


def _build_openai(config: AlfredConfig) -> EmbeddingProvider:
    # Imported inside the builder so the in-process path never pays for httpx
    # (~56ms, and the memory ingestor imports it nowhere else). The saving runs
    # one way only: this module's sibling imports embedding_provider at module
    # scope for the ABC, so the openai path loads it regardless — and that import
    # is cheap anyway (~27ms, no torch, no numpy; torch arrives inside
    # SentenceTransformerProvider._load()).
    from core.memory.openai_embedding_provider import OpenAICompatEmbeddingProvider

    return OpenAICompatEmbeddingProvider(
        model_name=config.embedding_model,
        host=config.embedding_host,
        dim=config.embedding_dim,
        api_key=config.embedding_api_key,
        timeout=config.embedding_timeout_seconds,
    )


# A registry, not a name list plus branches: an accepted name and its builder are the
# same entry, so a backend cannot be added here and then silently fall through to
# another one's provider. Keyed off DEFAULT_EMBEDDING_BACKEND so the default can never
# drift from shared/config.py and become unbuildable.
_BACKENDS: dict[str, Callable[[AlfredConfig], EmbeddingProvider]] = {
    DEFAULT_EMBEDDING_BACKEND: _build_sentence_transformers,
    "openai": _build_openai,
}


def build_embedding_provider(config: AlfredConfig) -> EmbeddingProvider:
    """Construct the embedding provider named by ``config.embedding_backend``.

    The caller owns the returned provider and should ``await provider.aclose()`` on
    shutdown. That matters even though the default backend holds nothing: the ``openai``
    one owns an httpx connection pool, and a caller holding the ``EmbeddingProvider``
    type cannot see which it got — so closing has to be unconditional, or a config
    change silently starts leaking pools.
    """
    # from_env already normalises, so this is for hand-built configs (tests, embedders).
    backend = config.embedding_backend.strip().lower() or DEFAULT_EMBEDDING_BACKEND
    builder = _BACKENDS.get(backend)
    if builder is None:
        raise RuntimeError(
            # Both spellings: the normalised name is what was matched, but only the raw
            # one greps against the user's .env ("Sentence Transformers" normalises to
            # "sentence transformers", a string that appears nowhere they can fix).
            f"Unknown EMBEDDING_BACKEND {config.embedding_backend!r} "
            f"(read as {backend!r}; expected one of: {', '.join(_BACKENDS)})"
        )
    return builder(config)
