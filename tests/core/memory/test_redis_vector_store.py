"""Tests for RedisVectorStore."""

from __future__ import annotations

import struct
from unittest.mock import AsyncMock

import pytest

from core.memory.redis_vector_store import RedisVectorStore, _pack_floats
from core.memory.vector_store import ContextMetadata

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_redis() -> AsyncMock:
    redis = AsyncMock()
    redis.execute_command = AsyncMock()
    redis.hset = AsyncMock()
    redis.delete = AsyncMock()
    redis.exists = AsyncMock(return_value=0)
    return redis


@pytest.fixture
def store(mock_redis: AsyncMock) -> RedisVectorStore:
    return RedisVectorStore(redis=mock_redis, dim=4)


def _meta() -> ContextMetadata:
    return ContextMetadata(
        type="episodic",
        source="conversation",
        entities="light.kitchen",
        timestamp=1711000000.0,
        significance=0.5,
        retrieval_count=0,
    )


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


def test_pack_floats_produces_correct_bytes() -> None:
    values = [1.0, 2.0, 3.0, 4.0]
    result = _pack_floats(values)
    unpacked = struct.unpack("<4f", result)
    assert list(unpacked) == pytest.approx(values)


def test_pack_floats_length() -> None:
    values = [0.1, 0.2, 0.3, 0.4]
    result = _pack_floats(values)
    # 4 floats x 4 bytes each = 16 bytes
    assert len(result) == 16


# ---------------------------------------------------------------------------
# ensure_index tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_index_creates_ft_index(
    store: RedisVectorStore, mock_redis: AsyncMock
) -> None:
    await store.ensure_index()
    mock_redis.execute_command.assert_called_once()
    call_args = mock_redis.execute_command.call_args[0]
    assert call_args[0] == "FT.CREATE"
    assert store._index_ready is True


@pytest.mark.asyncio
async def test_ensure_index_idempotent(store: RedisVectorStore, mock_redis: AsyncMock) -> None:
    """Second call to ensure_index should not call execute_command again."""
    store._index_ready = True
    await store.ensure_index()
    mock_redis.execute_command.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_index_handles_already_exists(
    store: RedisVectorStore, mock_redis: AsyncMock
) -> None:
    mock_redis.execute_command.side_effect = Exception("Index already exists")
    await store.ensure_index()
    assert store._index_ready is True


@pytest.mark.asyncio
async def test_ensure_index_handles_redisearch_unavailable(
    store: RedisVectorStore, mock_redis: AsyncMock
) -> None:
    mock_redis.execute_command.side_effect = Exception("unknown command FT.CREATE")
    await store.ensure_index()
    assert store._index_ready is False


# ---------------------------------------------------------------------------
# add() tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_creates_hash(store: RedisVectorStore, mock_redis: AsyncMock) -> None:
    await store.add(
        id="ep-1",
        content="test content",
        semantic_key="test key",
        embedding_content=[0.1, 0.2, 0.3, 0.4],
        embedding_semantic=[0.5, 0.6, 0.7, 0.8],
        metadata=_meta(),
    )
    mock_redis.hset.assert_called_once()


@pytest.mark.asyncio
async def test_add_uses_correct_key_prefix(store: RedisVectorStore, mock_redis: AsyncMock) -> None:
    store._index_ready = True
    await store.add(
        id="ep-2",
        content="hello",
        semantic_key="key",
        embedding_content=[0.1, 0.2, 0.3, 0.4],
        embedding_semantic=[0.1, 0.2, 0.3, 0.4],
        metadata=_meta(),
    )
    call_args = mock_redis.hset.call_args
    key = call_args[0][0]
    assert key == "ctx:ep-2"


@pytest.mark.asyncio
async def test_add_packs_embeddings_as_bytes(
    store: RedisVectorStore, mock_redis: AsyncMock
) -> None:
    store._index_ready = True
    emb = [0.1, 0.2, 0.3, 0.4]
    await store.add(
        id="ep-3",
        content="x",
        semantic_key="y",
        embedding_content=emb,
        embedding_semantic=emb,
        metadata=_meta(),
    )
    mapping = mock_redis.hset.call_args[1]["mapping"]
    assert isinstance(mapping["embedding_content"], bytes)
    assert isinstance(mapping["embedding_semantic"], bytes)


# ---------------------------------------------------------------------------
# delete() tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_removes_hash(store: RedisVectorStore, mock_redis: AsyncMock) -> None:
    await store.delete("ep-1")
    mock_redis.delete.assert_called_once()


@pytest.mark.asyncio
async def test_delete_uses_correct_key(store: RedisVectorStore, mock_redis: AsyncMock) -> None:
    await store.delete("ep-99")
    mock_redis.delete.assert_called_once_with("ctx:ep-99")


# ---------------------------------------------------------------------------
# exists() tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exists_returns_false_when_key_missing(
    store: RedisVectorStore, mock_redis: AsyncMock
) -> None:
    mock_redis.exists.return_value = 0
    assert await store.exists("ep-1") is False


@pytest.mark.asyncio
async def test_exists_checks_hash(store: RedisVectorStore, mock_redis: AsyncMock) -> None:
    mock_redis.exists.return_value = 1
    assert await store.exists("ep-1") is True


@pytest.mark.asyncio
async def test_exists_uses_correct_key(store: RedisVectorStore, mock_redis: AsyncMock) -> None:
    mock_redis.exists.return_value = 1
    await store.exists("ep-77")
    mock_redis.exists.assert_called_once_with("ctx:ep-77")


# ---------------------------------------------------------------------------
# count() tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_count_returns_zero_when_index_not_ready(
    store: RedisVectorStore, mock_redis: AsyncMock
) -> None:
    # Simulate RediSearch unavailable
    mock_redis.execute_command.side_effect = Exception("unknown command")
    result = await store.count()
    assert result == 0


@pytest.mark.asyncio
async def test_count_parses_ft_info(store: RedisVectorStore, mock_redis: AsyncMock) -> None:
    store._index_ready = True
    # FT.INFO returns flat alternating list
    mock_redis.execute_command.return_value = [
        b"index_name",
        b"idx:context",
        b"num_docs",
        b"42",
        b"max_doc_id",
        b"42",
    ]
    result = await store.count()
    assert result == 42


# ---------------------------------------------------------------------------
# search() tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_returns_empty_when_index_not_ready(
    store: RedisVectorStore, mock_redis: AsyncMock
) -> None:
    # Index never becomes ready
    mock_redis.execute_command.side_effect = Exception("unknown command")
    results = await store.search(query_embedding=[0.1, 0.2, 0.3, 0.4], limit=5)
    assert results == []


@pytest.mark.asyncio
async def test_search_issues_two_knn_queries(
    store: RedisVectorStore, mock_redis: AsyncMock
) -> None:
    store._index_ready = True
    # Return empty result sets for both KNN calls
    mock_redis.execute_command.return_value = [0]
    results = await store.search(query_embedding=[0.1, 0.2, 0.3, 0.4], limit=5)
    assert results == []
    # Two FT.SEARCH calls (one per embedding field)
    assert mock_redis.execute_command.call_count == 2
    for call in mock_redis.execute_command.call_args_list:
        assert call[0][0] == "FT.SEARCH"


@pytest.mark.asyncio
async def test_search_merges_by_max_score(store: RedisVectorStore, mock_redis: AsyncMock) -> None:
    store._index_ready = True

    def _make_ft_result(doc_id: str, distance: float) -> list[object]:
        """Simulate FT.SEARCH response for a single document."""
        return [
            1,
            f"ctx:{doc_id}".encode(),
            [
                b"content",
                b"some content",
                b"semantic_key",
                b"key",
                b"type",
                b"episodic",
                b"source",
                b"conversation",
                b"entities",
                b"light.kitchen",
                b"timestamp",
                b"1711000000.0",
                b"significance",
                b"0.5",
                b"retrieval_count",
                b"0",
                b"last_retrieved",
                b"0.0",
                b"compressed",
                b"",
                b"__score",
                str(distance).encode(),
            ],
        ]

    # content search returns distance 0.2 (similarity 0.8)
    # semantic search returns distance 0.1 (similarity 0.9) — higher, should win
    mock_redis.execute_command.side_effect = [
        _make_ft_result("ep-1", 0.2),
        _make_ft_result("ep-1", 0.1),
    ]

    results = await store.search(query_embedding=[0.1, 0.2, 0.3, 0.4], limit=5)
    assert len(results) == 1
    assert results[0].id == "ep-1"
    assert results[0].score == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_search_filters_by_min_similarity(
    store: RedisVectorStore, mock_redis: AsyncMock
) -> None:
    store._index_ready = True

    ft_result = [
        1,
        b"ctx:ep-1",
        [
            b"content",
            b"x",
            b"semantic_key",
            b"k",
            b"type",
            b"episodic",
            b"source",
            b"s",
            b"entities",
            b"e",
            b"timestamp",
            b"0",
            b"significance",
            b"0.1",
            b"retrieval_count",
            b"0",
            b"last_retrieved",
            b"0.0",
            b"compressed",
            b"",
            b"__score",
            b"0.95",  # distance=0.95 → similarity=0.05, below threshold
        ],
    ]
    mock_redis.execute_command.side_effect = [ft_result, ft_result]
    results = await store.search(query_embedding=[0.1, 0.2, 0.3, 0.4], limit=5, min_similarity=0.5)
    assert results == []


# ---------------------------------------------------------------------------
# update_metadata() tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_metadata_calls_hset(store: RedisVectorStore, mock_redis: AsyncMock) -> None:
    """update_metadata should HSET the given fields on the entry's Redis hash."""
    store._index_ready = True
    await store.update_metadata("ep-1", {"retrieval_count": 5, "last_retrieved": 1711000000.0})
    mock_redis.hset.assert_called_once_with(
        "ctx:ep-1", mapping={"retrieval_count": 5, "last_retrieved": 1711000000.0}
    )
