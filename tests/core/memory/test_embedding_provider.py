from __future__ import annotations

import pytest

from core.memory.embedding_provider import EmbeddingProvider, SentenceTransformerProvider


@pytest.fixture
def provider() -> SentenceTransformerProvider:
    # Use small model for tests to avoid downloading large model
    return SentenceTransformerProvider(model_name="all-MiniLM-L6-v2")


def test_embed_returns_list_of_floats(provider: SentenceTransformerProvider) -> None:
    result = provider.embed_sync("hello world")
    assert isinstance(result, list)
    assert all(isinstance(x, float) for x in result)


def test_embed_dimension_matches(provider: SentenceTransformerProvider) -> None:
    result = provider.embed_sync("hello world")
    assert len(result) == provider.dimension()


def test_embed_batch(provider: SentenceTransformerProvider) -> None:
    results = provider.embed_batch_sync(["hello", "world"])
    assert len(results) == 2
    assert len(results[0]) == provider.dimension()


def test_model_name(provider: SentenceTransformerProvider) -> None:
    assert provider.model_name() == "all-MiniLM-L6-v2"


@pytest.mark.asyncio
async def test_async_embed(provider: SentenceTransformerProvider) -> None:
    result = await provider.embed("hello world")
    assert len(result) == provider.dimension()


class RecordingProvider(EmbeddingProvider):
    """Minimal concrete provider — pins what the ABC gives a backend for free."""

    def __init__(self) -> None:
        self.embedded: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.embedded.append(text)
        return [0.0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    def dimension(self) -> int:
        return 1

    def model_name(self) -> str:
        return "recording"


@pytest.mark.asyncio
async def test_warmup_defaults_to_one_embed() -> None:
    """The ABC default must stay equivalent to the ``lambda: embedder.embed("warmup")``
    that services used to hand start_warmup(); services now pass ``.warmup`` instead."""
    provider = RecordingProvider()
    await provider.warmup()
    assert provider.embedded == ["warmup"]


@pytest.mark.asyncio
async def test_aclose_defaults_to_a_noop() -> None:
    """On the ABC so services can release a provider's resources through the base type;
    a backend holding nothing (sentence-transformers) simply has nothing to do."""
    provider = RecordingProvider()
    await provider.aclose()
    assert provider.embedded == []
