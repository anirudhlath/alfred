"""Voice processing must never block the channels event loop.

Whisper transcription, Piper synthesis, and model construction are CPU-bound
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


@pytest.mark.asyncio
async def test_aget_tts_returns_none_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No registered TTS backend available (missing voice extras) must surface as
    None, not raise."""
    voice_models._lazy_cache.clear()

    def fake_construct_backend(module: str, cls_name: str, missing_msg: str) -> Any:
        return None

    monkeypatch.setattr(voice_models, "_construct_backend", fake_construct_backend)

    assert await web_server.aget_tts() is None
    # Cached failure short-circuits without re-entering the loader
    assert await web_server.aget_tts() is None
    voice_models._lazy_cache.clear()


@pytest.mark.asyncio
async def test_aget_speaker_id_double_checks_cache_after_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two concurrent first calls must not each build their own SpeakerID
    (each would later load its own ECAPA model). A caller that loses the race
    for the module-level lock must re-check the cache once it acquires it,
    rather than constructing a second instance."""
    constructions = 0

    class _FakeSpeakerID:
        def __init__(self, redis: Any) -> None:
            nonlocal constructions
            constructions += 1

    monkeypatch.setattr(voice_models, "_get_speaker_id_cls", lambda: _FakeSpeakerID)

    sentinel = object()
    async with voice_models._speaker_id_lock:
        task = asyncio.create_task(voice_models.aget_speaker_id(redis=None))
        await asyncio.sleep(0)  # let the task start and block waiting for the lock
        # Simulate a winner (holding the lock) already having cached an instance.
        voice_models._lazy_cache["speaker_id"] = sentinel

    result = await task

    assert result is sentinel
    assert constructions == 0  # double-check found the cache — didn't build a second one
