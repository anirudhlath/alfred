"""SpeakerID — voiceprint enroll/identify with injected embedder."""

from unittest.mock import AsyncMock

import numpy as np

from core.voice.speaker_id import SpeakerID


def _unit(v: list[float]) -> np.ndarray:
    arr = np.array(v, dtype=np.float32)
    return arr / np.linalg.norm(arr)


def _fake_redis(store: dict[bytes, bytes]) -> AsyncMock:
    redis = AsyncMock()

    async def _hset(key: str, field: str, value: bytes) -> int:
        store[field.encode()] = value
        return 1

    async def _hgetall(key: str) -> dict[bytes, bytes]:
        return dict(store)

    redis.hset = AsyncMock(side_effect=_hset)
    redis.hgetall = AsyncMock(side_effect=_hgetall)
    return redis


def _speaker_id(store: dict[bytes, bytes], embeddings: dict[bytes, list[float]]) -> SpeakerID:
    """embed_fn maps exact pcm bytes → fixed unit vectors."""

    def embed(pcm: bytes) -> np.ndarray:
        return _unit(embeddings[pcm])

    return SpeakerID(_fake_redis(store), threshold=0.45, embed_fn=embed)


async def test_enroll_stores_normalized_mean() -> None:
    store: dict[bytes, bytes] = {}
    sid = _speaker_id(store, {b"s1": [1.0, 0.0], b"s2": [0.0, 1.0]})
    assert await sid.enroll("sir", [b"s1", b"s2"]) is True
    stored = np.frombuffer(store[b"sir"], dtype=np.float32)
    assert np.allclose(np.linalg.norm(stored), 1.0, atol=1e-5)


async def test_identify_match_above_threshold() -> None:
    store: dict[bytes, bytes] = {}
    sid = _speaker_id(store, {b"enroll": [1.0, 0.0], b"query": [0.95, 0.1]})
    await sid.enroll("sir", [b"enroll"])
    match = await sid.identify(b"query")
    assert match.identity == "sir"
    assert match.enrolled is True
    assert 0.7 <= match.confidence <= 0.95


async def test_identify_below_threshold_is_unknown() -> None:
    store: dict[bytes, bytes] = {}
    sid = _speaker_id(store, {b"enroll": [1.0, 0.0], b"query": [0.0, 1.0]})
    await sid.enroll("sir", [b"enroll"])
    match = await sid.identify(b"query")
    assert match == await sid.identify(b"query")  # deterministic
    assert match.identity == "unknown"
    assert match.enrolled is False
    assert match.confidence == 0.0


async def test_identify_with_no_enrollments() -> None:
    sid = _speaker_id({}, {b"q": [1.0, 0.0]})
    match = await sid.identify(b"q")
    assert match.identity == "unknown"


async def test_identify_picks_best_of_multiple() -> None:
    store: dict[bytes, bytes] = {}
    sid = _speaker_id(store, {b"a": [1.0, 0.0], b"b": [0.0, 1.0], b"q": [0.9, 0.44]})
    await sid.enroll("sir", [b"a"])
    await sid.enroll("guest_bob", [b"b"])
    assert (await sid.identify(b"q")).identity == "sir"
