"""Shared lazy voice-model loaders for the channels process."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Callable

_lazy_cache: dict[str, Any] = {}
_FAILED: object = object()  # sentinel for imports that already failed


def _lazy_load(key: str, module: str, cls_name: str, missing_msg: str) -> Any:
    """Lazy-load a class from an optional module. Returns instance or None on failure."""
    cached = _lazy_cache.get(key)
    if cached is _FAILED:
        return None
    if cached is not None:
        return cached
    try:
        import importlib

        mod = importlib.import_module(module)
        instance = getattr(mod, cls_name)()
        _lazy_cache[key] = instance
        return instance
    except ImportError:
        logger.warning("{} — {} disabled", missing_msg, key)
        _lazy_cache[key] = _FAILED
    except Exception as exc:
        logger.error("Failed to initialise {}: {}", cls_name, exc)
        _lazy_cache[key] = _FAILED
    return None


def _get_stt() -> Any:
    """Lazy-load WhisperSTT (requires voice extra)."""
    return _lazy_load("stt", "core.voice.stt", "WhisperSTT", "faster-whisper not installed")


def _get_tts() -> Any:
    """Lazy-load PiperTTS (requires voice extra)."""
    return _lazy_load("tts", "core.voice.tts", "PiperTTS", "piper-tts not installed")


# Model construction takes 10-40s and must run off the event loop; the lock
# keeps a warmup task and a first request from loading the same model twice.
_voice_load_lock = asyncio.Lock()


async def _aget_voice(key: str, getter: Callable[[], Any]) -> Any:
    cached = _lazy_cache.get(key)
    if cached is not None:
        return None if cached is _FAILED else cached
    async with _voice_load_lock:
        return await asyncio.to_thread(getter)


async def _aget_stt() -> Any:
    """WhisperSTT instance (or None), constructed off the event loop."""
    return await _aget_voice("stt", _get_stt)


async def _aget_tts() -> Any:
    """PiperTTS instance (or None), constructed off the event loop."""
    return await _aget_voice("tts", _get_tts)


async def _transcribe_async(stt: Any, audio_bytes: bytes, audio_fmt: str) -> str:
    """Run blocking Whisper transcription in a worker thread."""
    result = await asyncio.to_thread(stt.transcribe, audio_bytes, audio_format=audio_fmt)
    return cast("str", result)


async def _synthesize_async(tts: Any, text: str) -> bytes:
    """Run blocking Piper synthesis in a worker thread."""
    result = await asyncio.to_thread(tts.synthesize, text)
    return cast("bytes", result)


def _get_speaker_id_cls() -> Any:
    """Lazy-import SpeakerID class (requires voice extra deps at embed time)."""
    from core.voice.speaker_id import SpeakerID

    return SpeakerID


async def aget_speaker_id(redis: Any) -> Any | None:
    """Shared SpeakerID singleton, or None if the voice extra is unavailable."""
    cached = _lazy_cache.get("speaker_id")
    if cached is _FAILED:
        return None
    if cached is not None:
        return cached
    try:
        instance = _get_speaker_id_cls()(redis)
    except ImportError:
        logger.warning("speechbrain not installed — speaker ID disabled")
        _lazy_cache["speaker_id"] = _FAILED
        return None
    _lazy_cache["speaker_id"] = instance
    return instance
