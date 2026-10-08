"""Voice processing must never block the channels event loop.

Whisper transcription, TTS synthesis, and model construction are CPU-bound
(seconds to tens of seconds) — they must run via asyncio.to_thread so the
event loop keeps serving WebSockets, admin API, and notification delivery.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest

from core.channels import voice_models, web_server


@pytest.fixture(autouse=True)
def _clean_lazy_cache() -> Any:
    voice_models._lazy_cache.clear()
    yield
    voice_models._lazy_cache.clear()


class _ThreadRecorder:
    """Fake STT/TTS that records which thread its blocking method ran on."""

    def __init__(self) -> None:
        self.thread_ids: list[int] = []

    def transcribe(self, audio_bytes: bytes, audio_format: str = "wav") -> str:
        self.thread_ids.append(threading.get_ident())
        return "transcribed"

    def synthesize(self, text: str) -> bytes:
        self.thread_ids.append(threading.get_ident())
        return b"RIFFwav"


@pytest.mark.asyncio
async def test_transcribe_async_runs_off_event_loop() -> None:
    fake = _ThreadRecorder()

    result = await web_server.transcribe_async(fake, b"audio", "wav")

    assert result == "transcribed"
    assert fake.thread_ids and fake.thread_ids[0] != threading.get_ident()


@pytest.mark.asyncio
async def test_synthesize_async_runs_off_event_loop() -> None:
    fake = _ThreadRecorder()

    result = await web_server.synthesize_async(fake, "hello sir")

    assert result == b"RIFFwav"
    assert fake.thread_ids and fake.thread_ids[0] != threading.get_ident()


@pytest.mark.asyncio
async def test_aget_stt_constructs_in_worker_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """Model construction (10-40s load) must not run on the event loop thread."""
    construction_threads: list[int] = []
    instance = object()

    def fake_lazy_load(key: str, module: str, cls_name: str, missing_msg: str) -> Any:
        cached = voice_models._lazy_cache.get(key)
        if cached is not None:
            return cached
        construction_threads.append(threading.get_ident())
        voice_models._lazy_cache[key] = instance
        return instance

    monkeypatch.setattr(voice_models, "_lazy_load", fake_lazy_load)

    result = await web_server.aget_stt()

    assert result is instance
    assert construction_threads and construction_threads[0] != threading.get_ident()


@pytest.mark.asyncio
async def test_aget_stt_concurrent_calls_construct_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Warmup racing a first request must not load the model twice."""
    constructions = 0
    instance = object()

    def fake_lazy_load(key: str, module: str, cls_name: str, missing_msg: str) -> Any:
        nonlocal constructions
        cached = voice_models._lazy_cache.get(key)
        if cached is not None:
            return cached
        constructions += 1
        time.sleep(0.05)  # simulate slow model load
        voice_models._lazy_cache[key] = instance
        return instance

    monkeypatch.setattr(voice_models, "_lazy_load", fake_lazy_load)

    results = await asyncio.gather(web_server.aget_stt(), web_server.aget_stt())

    assert results == [instance, instance]
    assert constructions == 1


class _CountingLoader:
    """Fake ``_lazy_load``: a slow, unlocked, cache-checking construct.

    It holds no lock of its own, as the real getters hold none, so a single
    construction under concurrency is the load lock's doing alone.
    """

    def __init__(self, delay: float = 0.05) -> None:
        self.instance = object()
        self.constructions = 0
        self.started = threading.Event()
        self.release = threading.Event()
        self._delay = delay

    def __call__(self, key: str, module: str, cls_name: str, missing_msg: str) -> Any:
        cached = voice_models._lazy_cache.get(key)
        if cached is not None:
            return cached
        self.constructions += 1
        self.started.set()
        time.sleep(self._delay)  # a model load, long enough for callers to overlap
        self.release.wait(timeout=5)
        voice_models._lazy_cache[key] = self.instance
        return self.instance


@pytest.mark.asyncio
async def test_aget_voice_constructs_once_when_the_getter_skips_the_cache() -> None:
    """A getter only has to write the cache: callers queued behind its load re-check
    the cache under the lock, so they never run it a second time."""
    constructions = 0
    instance = object()

    def getter() -> object:
        nonlocal constructions
        constructions += 1
        time.sleep(0.05)  # a model load, long enough for callers to overlap
        voice_models._lazy_cache["probe"] = instance
        return instance

    results = await asyncio.gather(*(voice_models._aget_voice("probe", getter) for _ in range(4)))

    assert results == [instance] * 4
    assert constructions == 1


def test_aget_stt_works_on_every_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Issue #97: the load lock must not bind to the first loop that contends on it.

    Each ``asyncio.run`` is a fresh loop, as each real-lifespan ``TestClient`` is.
    Two first calls contend the lock on the first loop; clearing the cache makes them
    contend it again on a second one. While the lock was a module-level
    ``asyncio.Lock``, the second loop raised "is bound to a different event loop".
    """
    loader = _CountingLoader()
    loader.release.set()
    monkeypatch.setattr(voice_models, "_lazy_load", loader)

    async def _two_first_calls() -> list[Any]:
        return list(await asyncio.gather(voice_models.aget_stt(), voice_models.aget_stt()))

    for _ in range(2):
        voice_models._lazy_cache.clear()
        assert asyncio.run(_two_first_calls()) == [loader.instance, loader.instance]

    assert loader.constructions == 2  # one per loop: each round started from a cold cache


@pytest.mark.asyncio
async def test_cancelled_caller_does_not_start_a_second_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelling a caller cannot cancel its worker thread (``asyncio.to_thread``
    never can), so the load it started runs on. The lock must stay held for as long
    as that load does, or the next caller starts a second load beside it."""
    loader = _CountingLoader(delay=0)
    monkeypatch.setattr(voice_models, "_lazy_load", loader)

    first = asyncio.create_task(voice_models.aget_stt())
    assert await asyncio.to_thread(loader.started.wait, 5)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    second = asyncio.create_task(voice_models.aget_stt())
    await asyncio.sleep(0.1)  # time enough for a second load to begin, were it free to
    loader.release.set()

    assert await second is loader.instance
    assert loader.constructions == 1


@pytest.mark.asyncio
async def test_aget_tts_returns_none_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No registered TTS backend available (missing voice extras) must surface as
    None, not raise."""
    construct_calls = 0

    def fake_construct_backend(module: str, cls_name: str, missing_msg: str) -> Any:
        nonlocal construct_calls
        construct_calls += 1
        # Every backend's dependency is "absent" (not a runtime failure), so the
        # total failure is cached permanently (see voice_models.get_tts) and a
        # second call can short-circuit without re-entering the loader.
        return voice_models._ConstructResult(None, import_missing=True)

    monkeypatch.setattr(voice_models, "_construct_backend", fake_construct_backend)

    assert await web_server.aget_tts() is None
    calls_after_first = construct_calls
    assert calls_after_first > 0

    # Cached failure short-circuits without re-entering the loader.
    assert await web_server.aget_tts() is None
    assert construct_calls == calls_after_first


@pytest.mark.asyncio
async def test_aget_speaker_id_concurrent_first_calls_construct_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent first calls must not each build their own SpeakerID (each would
    later load its own ECAPA model). aget_speaker_id takes no lock: it never awaits,
    so its check-construct-cache runs as one step no other task can interleave. An
    await added between the check and the cache write would let every caller here
    through to construct its own."""
    constructions = 0

    class _FakeSpeakerID:
        def __init__(self, redis: Any) -> None:
            nonlocal constructions
            constructions += 1

    monkeypatch.setattr(voice_models, "_get_speaker_id_cls", lambda: _FakeSpeakerID)

    results = await asyncio.gather(*(voice_models.aget_speaker_id(redis=None) for _ in range(4)))

    assert constructions == 1
    assert isinstance(results[0], _FakeSpeakerID)
    assert all(result is results[0] for result in results)
