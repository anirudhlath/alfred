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


def get_stt() -> Any:
    """Lazy-load WhisperSTT (requires voice extra)."""
    return _lazy_load("stt", "core.voice.stt", "WhisperSTT", "faster-whisper not installed")


def get_tts() -> Any:
    """Lazy-load the configured TTS backend (Kokoro default; Piper fallback).

    Reads ``config.tts_backend``, tries that adapter first, then falls back to any
    other registered backend whose optional deps are installed. Returns a
    ``TTSBackend`` instance (typed Any for parity with get_stt), cached under "tts".
    """
    cached = _lazy_cache.get("tts")
    if cached is _FAILED:
        return None
    if cached is not None:
        return cached

    from core.voice.tts_registry import TTS_BACKENDS, resolve_backend_order
    from shared.config import AlfredConfig

    selected = AlfredConfig.from_env().tts_backend
    for name in resolve_backend_order(selected):
        module, cls_name, missing_msg = TTS_BACKENDS[name]
        instance = _construct_backend(module, cls_name, missing_msg)
        if instance is not None:
            _lazy_cache["tts"] = instance
            return instance
    _lazy_cache["tts"] = _FAILED
    return None


def _construct_backend(module: str, cls_name: str, missing_msg: str) -> Any:
    """Import + instantiate a TTS backend adapter; return None (logged) on failure."""
    try:
        import importlib

        mod = importlib.import_module(module)
        return getattr(mod, cls_name)()
    except ImportError:
        logger.warning("{} — {} unavailable", missing_msg, cls_name)
    except Exception as exc:
        logger.error("Failed to initialise {}: {}", cls_name, exc)
    return None


# Model construction takes 10-40s and must run off the event loop; the lock
# keeps a warmup task and a first request from loading the same model twice.
_voice_load_lock = asyncio.Lock()


async def _aget_voice(key: str, getter: Callable[[], Any]) -> Any:
    cached = _lazy_cache.get(key)
    if cached is not None:
        return None if cached is _FAILED else cached
    async with _voice_load_lock:
        return await asyncio.to_thread(getter)


async def aget_stt() -> Any:
    """WhisperSTT instance (or None), constructed off the event loop."""
    return await _aget_voice("stt", get_stt)


async def aget_tts() -> Any:
    """Configured TTS backend instance (or None), constructed off the event loop."""
    return await _aget_voice("tts", get_tts)


async def transcribe_async(stt: Any, audio_bytes: bytes, audio_fmt: str) -> str:
    """Run blocking Whisper transcription in a worker thread."""
    result = await asyncio.to_thread(stt.transcribe, audio_bytes, audio_format=audio_fmt)
    return cast("str", result)


async def synthesize_async(tts: Any, text: str) -> bytes:
    """Run blocking TTS synthesis in a worker thread."""
    result = await asyncio.to_thread(tts.synthesize, text)
    return cast("bytes", result)


def _get_speaker_id_cls() -> Any:
    """Lazy-import SpeakerID class (requires voice extra deps at embed time)."""
    from core.voice.speaker_id import SpeakerID

    return SpeakerID


# Guards construct-and-cache the same way _voice_load_lock guards get_stt/get_tts:
# without it, two concurrent first callers could each build their own SpeakerID
# (each would later load its own ECAPA model). A dedicated lock (not
# _voice_load_lock) so speaker-ID construction never queues behind a slow
# STT/TTS model load.
_speaker_id_lock = asyncio.Lock()


async def aget_speaker_id(redis: Any) -> Any | None:
    """Shared SpeakerID singleton, or None if the voice extra is unavailable."""
    cached = _lazy_cache.get("speaker_id")
    if cached is not None:
        return None if cached is _FAILED else cached
    async with _speaker_id_lock:
        # Double-check: another caller may have constructed it while we
        # waited for the lock.
        cached = _lazy_cache.get("speaker_id")
        if cached is not None:
            return None if cached is _FAILED else cached
        try:
            instance = _get_speaker_id_cls()(redis)
        except ImportError:
            logger.warning("speechbrain not installed — speaker ID disabled")
            _lazy_cache["speaker_id"] = _FAILED
            return None
        _lazy_cache["speaker_id"] = instance
        return instance
