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
    from core.memory.embedding_provider import EmbeddingProvider
    from shared.config import AlfredConfig

_BACKENDS = ("sentence_transformers", "openai")


def build_embedding_provider(config: AlfredConfig) -> EmbeddingProvider:
    """Construct the embedding provider named by ``config.embedding_backend``."""
    backend = config.embedding_backend.strip().lower() or DEFAULT_EMBEDDING_BACKEND
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
            api_key=config.embedding_api_key,
        )

    from core.memory.embedding_provider import SentenceTransformerProvider

    return SentenceTransformerProvider(config.embedding_model)
