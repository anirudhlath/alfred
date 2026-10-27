"""The eval's lenses on the real stack: counting, read-only recall, and SCAN."""

from __future__ import annotations

import struct
from unittest.mock import AsyncMock

import pytest

from core.memory.vector_store import ContextMetadata, SearchResult
from evals.memory.env import CountingEmbedder, ReadOnlyStore, scan_hot, stored_vectors
from tests.evals.memory_fakes import FakeRedis, HashEmbedder


async def test_counting_embedder_counts_every_call_by_phase() -> None:
    inner = HashEmbedder()
    embedder = CountingEmbedder(inner)
    embedder.phase = "write"
    await embedder.embed("a")
    await embedder.embed("a")
    embedder.phase = "decay"
    await embedder.embed("a")
    assert embedder.calls == {"write": 2, "decay": 1}


async def test_writes_hit_the_cache_but_decay_passes_never_do() -> None:
    inner = HashEmbedder()
    embedder = CountingEmbedder(inner)
    embedder.phase = "write"
    first = await embedder.embed("hallway light on")
    assert await embedder.embed("hallway light on") == first
    assert inner.calls == ["hallway light on"]

    embedder.phase = "decay"
    await embedder.embed("hallway light on")
    await embedder.embed("hallway light on")
    # Decay cost is the point of the measurement: every call reaches the model.
    assert inner.calls == ["hallway light on"] * 3


async def test_reset_counts_keeps_the_cache() -> None:
    inner = HashEmbedder()
    embedder = CountingEmbedder(inner)
    embedder.phase = "write"
    await embedder.embed("x")
    embedder.reset_counts()
    await embedder.embed("x")
    assert embedder.calls == {"write": 1}
    assert inner.calls == ["x"]


async def test_read_only_store_searches_but_never_writes() -> None:
    inner = AsyncMock()
    inner.search = AsyncMock(return_value=["hit"])
    store = ReadOnlyStore(inner)

    assert await store.search([0.1], 10) == ["hit"]
    await store.update_metadata("m1", {"retrieval_count": 1})
    assert store.suppressed_writes == 1
    inner.update_metadata.assert_not_awaited()
    with pytest.raises(RuntimeError):
        await store.delete("m1")
    with pytest.raises(RuntimeError):
        await store.add(
            "m1",
            "c",
            "k",
            [0.1],
            [0.1],
            ContextMetadata(
                type="episodic",
                source="s",
                entities="",
                timestamp=1.0,
                significance=0.1,
                retrieval_count=0,
            ),
        )


async def test_scan_hot_returns_every_entry_as_a_search_result() -> None:
    redis = FakeRedis(
        {
            "ctx:obs-1": {
                "type": "episodic",
                "source": "observation",
                "entities": "light.hallway",
                "timestamp": 1000.0,
                "significance": 0.105,
                "retrieval_count": 2,
                "last_retrieved": 1500.0,
                "compressed": "",
                "content": "[observation] light.hallway: off → on",
                "semantic_key": "Observed light.hallway change from off to on",
            },
            "ctx:sem:learned:0": {"type": "semantic", "content": "# Prefs", "timestamp": 0},
            "ctx:partial": {"retrieval_count": 3},  # a stray hash with no index fields
            "alfred:other": {"type": "episodic"},  # not under the context prefix
        }
    )
    results = await scan_hot(redis)  # type: ignore[arg-type]

    assert [r.id for r in results] == ["obs-1", "sem:learned:0"]
    obs = results[0]
    assert isinstance(obs, SearchResult)
    assert obs.metadata.significance == pytest.approx(0.105)
    assert obs.metadata.retrieval_count == 2
    assert obs.metadata.last_retrieved == 1500.0
    assert obs.content.endswith("off → on")


async def test_stored_vectors_takes_the_better_of_content_and_semantic() -> None:
    def blob(values: list[float]) -> bytes:
        return struct.pack(f"<{len(values)}f", *values)

    redis = FakeRedis(
        {
            "ctx:a": {
                "embedding_content": blob([1.0, 0.0]),
                "embedding_semantic": blob([0.0, 1.0]),
            },
            "ctx:b": {
                "embedding_content": blob([-1.0, 0.0]),
                "embedding_semantic": blob([-1.0, -1.0]),
            },
        }
    )
    vectors = await stored_vectors(redis, ["a", "b", "missing"])  # type: ignore[arg-type]

    assert vectors.ids == ["a", "b"]
    scores = vectors.similarity([0.0, 2.0])
    assert scores[0] == pytest.approx(1.0)  # semantic matches exactly
    assert scores[1] == pytest.approx(0.0)  # content is orthogonal, semantic is worse
