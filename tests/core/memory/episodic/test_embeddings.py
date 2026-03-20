"""Tests for embedding model wrapper."""

from __future__ import annotations

import pytest

from core.memory.episodic.embeddings import EmbeddingModel


@pytest.fixture(scope="module")
def model() -> EmbeddingModel:
    """Load model once for all tests in this module."""
    return EmbeddingModel()


def test_embed_returns_bytes(model: EmbeddingModel) -> None:
    result = model.embed("Sir asked for a morning briefing")
    assert isinstance(result, bytes)
    assert len(result) > 0


def test_embed_deterministic(model: EmbeddingModel) -> None:
    a = model.embed("hello world")
    b = model.embed("hello world")
    assert a == b


def test_cosine_similarity_same_text(model: EmbeddingModel) -> None:
    a = model.embed("the lights are on")
    b = model.embed("the lights are on")
    sim = model.cosine_similarity(a, b)
    assert sim > 0.99


def test_cosine_similarity_different_text(model: EmbeddingModel) -> None:
    a = model.embed("turn on the kitchen lights")
    b = model.embed("stock market performance today")
    sim = model.cosine_similarity(a, b)
    assert sim < 0.5
