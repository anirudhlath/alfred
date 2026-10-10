"""SpeakerID — voiceprint enroll/identify with injected embedder."""

from __future__ import annotations

import asyncio
import sys
import threading
import time
import types
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import numpy as np
import pytest

from core.voice.speaker_id import SpeakerID

if TYPE_CHECKING:
    from pathlib import Path


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


# --- ECAPA model loading (issue #97) ------------------------------------------------
#
# The ECAPA path runs with speechbrain's EncoderClassifier stubbed out: no download, no
# weights. ``aget_speaker_id`` shares one SpeakerID across the process, so its model
# load must be safe from any event loop and must survive a cancelled caller.

_PCM = np.zeros(1600, dtype=np.int16).tobytes()  # 0.1 s of 16 kHz silence


class _FakeECAPA:
    def encode_batch(self, wavs: Any) -> Any:
        import torch

        return torch.ones(1, 1, 4)


class _ECAPABuild:
    """Fake ``EncoderClassifier.from_hparams``: counts loads; can hold one mid-load."""

    def __init__(self) -> None:
        self.calls = 0
        self.fail = False
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def __call__(self, *, source: str, savedir: str, run_opts: dict[str, str]) -> _FakeECAPA:
        self.calls += 1
        self.started.set()
        time.sleep(0.05)  # a model load, long enough for callers to overlap
        self.release.wait(timeout=5)
        if self.fail:
            raise OSError("download failed")
        return _FakeECAPA()


@pytest.fixture
def ecapa_build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _ECAPABuild:
    build = _ECAPABuild()
    speaker_module = types.ModuleType("speechbrain.inference.speaker")
    speaker_module.EncoderClassifier = types.SimpleNamespace(from_hparams=build)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "speechbrain.inference.speaker", speaker_module)
    monkeypatch.setenv("ALFRED_MODELS_DIR", str(tmp_path))
    return build


async def test_injected_embed_fn_never_loads_ecapa(ecapa_build: _ECAPABuild) -> None:
    sid = _speaker_id({}, {_PCM: [1.0, 0.0]})
    assert await sid.enroll("sir", [_PCM]) is True
    assert ecapa_build.calls == 0


async def test_cancelled_caller_does_not_start_a_second_ecapa_load(
    ecapa_build: _ECAPABuild,
) -> None:
    """Cancelling a caller cannot stop the worker thread loading the model, so the
    load must hold its lock until it ends, or the next caller loads a second copy."""
    ecapa_build.release.clear()
    sid = SpeakerID(_fake_redis({}), threshold=0.45)

    first = asyncio.create_task(sid.enroll("sir", [_PCM]))
    assert await asyncio.to_thread(ecapa_build.started.wait, 5)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    second = asyncio.create_task(sid.enroll("sir", [_PCM]))
    await asyncio.sleep(0.1)  # time enough for a second load to begin, were it free to
    ecapa_build.release.set()

    assert await second is True
    assert ecapa_build.calls == 1


def test_ecapa_load_works_on_a_second_event_loop(ecapa_build: _ECAPABuild) -> None:
    """Two callers contend for the load on a first loop, where the download fails (so
    nothing is cached), then again on a second loop. While the load lock was an
    asyncio.Lock, it bound to the first loop and the second raised "is bound to a
    different event loop"."""
    sid = SpeakerID(_fake_redis({}), threshold=0.45)

    async def _two_enrolls() -> list[Any]:
        calls = (sid.enroll("sir", [_PCM]), sid.enroll("sir", [_PCM]))
        return list(await asyncio.gather(*calls, return_exceptions=True))

    ecapa_build.fail = True
    first_loop = asyncio.run(_two_enrolls())
    assert all(isinstance(result, OSError) for result in first_loop), first_loop

    ecapa_build.fail = False
    assert asyncio.run(_two_enrolls()) == [True, True]
    assert ecapa_build.calls == 3  # two failed loads on the first loop, one on the second
