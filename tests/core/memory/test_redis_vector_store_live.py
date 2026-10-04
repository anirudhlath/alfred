"""The hot store against a real Redis 8 query engine, in a throwaway container.

Opt-in (``ALFRED_MEMORY_EVAL_DOCKER=1``): these are the behaviours a mocked Redis cannot
show — RediSearch's default result window, and what HNSW does with duplicate vectors.
"""

from __future__ import annotations

import os
import shutil
from typing import TYPE_CHECKING

import numpy as np
import pytest

from core.memory.redis_vector_store import RedisVectorStore
from core.memory.vector_store import ContextMetadata, Range
from evals.memory.sandbox import RedisSandbox

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.skipif(
    os.getenv("ALFRED_MEMORY_EVAL_DOCKER") != "1" or shutil.which("docker") is None,
    reason="set ALFRED_MEMORY_EVAL_DOCKER=1 to run against a throwaway Redis container",
)

DIM = 16


@pytest.fixture
async def store() -> AsyncIterator[RedisVectorStore]:
    async with RedisSandbox() as redis:
        yield RedisVectorStore(redis=redis, dim=DIM)


def _unit(rng: np.random.Generator) -> list[float]:
    vector = rng.normal(size=DIM)
    return [float(x) for x in vector / np.linalg.norm(vector)]


def _meta(timestamp: float = 1_700_000_000.0, significance: float = 0.1) -> ContextMetadata:
    return ContextMetadata(
        type="episodic",
        source="observation",
        entities="",
        timestamp=timestamp,
        significance=significance,
        retrieval_count=0,
    )


async def _add(store: RedisVectorStore, memory_id: str, vector: list[float], **meta: float) -> None:
    await store.add(memory_id, memory_id, memory_id, vector, vector, _meta(**meta))


async def test_a_search_returns_as_many_results_as_it_asks_for(store: RedisVectorStore) -> None:
    rng = np.random.default_rng(0)
    for i in range(30):
        await _add(store, f"m{i}", _unit(rng))

    results = await store.search(_unit(rng), limit=25, min_similarity=-1.0)

    assert len(results) == 25


async def test_select_returns_every_match_across_pages(store: RedisVectorStore) -> None:
    rng = np.random.default_rng(1)
    for i in range(7):
        await _add(store, f"low{i}", _unit(rng), significance=0.1)
    await _add(store, "important", _unit(rng), significance=0.9)
    await _add(store, "section", _unit(rng), timestamp=0.0, significance=0.1)

    selected = await store.select(
        {"timestamp": Range(above=0.0), "significance": Range(below=0.4)}, page_size=3
    )

    assert sorted(r.id for r in selected) == [f"low{i}" for i in range(7)]
    assert selected[0].metadata.significance == pytest.approx(0.1)


async def test_a_search_finds_a_distinct_memory_among_thousands_of_duplicates(
    store: RedisVectorStore,
) -> None:
    """Passive observation repeats itself: the same state change, the same vector, hundreds
    of times. HNSW's greedy search gets trapped among the copies and misses the one memory
    that actually matches (EXP-006: the tool missed 73-90% of what exact search finds)."""
    rng = np.random.default_rng(2)
    bases = [_unit(rng) for _ in range(30)]
    pipe_vectors = [(f"dup{i}", bases[i % len(bases)]) for i in range(3000)]
    for memory_id, vector in pipe_vectors:
        await _add(store, memory_id, vector)
    target = _unit(rng)
    await _add(store, "target", target)
    query = [t + 0.05 * float(n) for t, n in zip(target, rng.normal(size=DIM), strict=True)]

    results = await store.search(query, limit=10)

    assert "target" in [r.id for r in results]
