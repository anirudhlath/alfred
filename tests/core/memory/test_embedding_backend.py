"""The EMBEDDING_BACKEND seam: one env var picks the provider every service builds."""

from __future__ import annotations

import dataclasses

import pytest

from core.memory.embedding_backend import build_embedding_provider
from shared.config import AlfredConfig


def _config(**overrides: object) -> AlfredConfig:
    # AlfredConfig is a frozen dataclass, so replace() gives a config carrying the
    # declared defaults with no env reads — these tests stay independent of the
    # developer's shell.
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
