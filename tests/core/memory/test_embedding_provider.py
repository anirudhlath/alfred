from __future__ import annotations

import pytest

from core.memory.embedding_provider import SentenceTransformerProvider


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
