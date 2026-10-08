"""Shared lazy voice-model loaders for the channels process."""

from __future__ import annotations

import asyncio
import importlib
import threading
from typing import TYPE_CHECKING, Any, NamedTuple, cast

from loguru import logger

from core.lazy import Lazy

if TYPE_CHECKING:
    from core.voice.tts_backend import TTSBackend


def _lazy_load(key: str, module: str, cls_name: str, missing_msg: str) -> Any:
    """Import a class from an optional module and construct it; None on failure.

    Blocks. The caller's ``Lazy`` caches the None, so a failure is never retried.
    """
    try:
        mod = importlib.import_module(module)
        return getattr(mod, cls_name)()
    except ImportError:
        logger.warning("{} — {} disabled", missing_msg, key)
    except Exception as exc:
        logger.error("Failed to initialise {}: {}", cls_name, exc)
    return None


def _build_stt() -> Any:
    return _lazy_load("stt", "core.voice.stt", "WhisperSTT", "faster-whisper not installed")


def get_stt() -> Any:
    """Lazy-load WhisperSTT (requires voice extra). Blocks while it loads."""
    return _stt.get()


class _ConstructResult(NamedTuple):
    """Outcome of attempting to construct one TTS backend adapter.

    ``import_missing`` distinguishes an absent optional dependency (expected —
    the caller silently proceeds to the next fallback) from a runtime
    construction failure (unexpected — the caller must log loudly and must
    not permanently cache the failure, since a later retry might succeed).
    """

    instance: TTSBackend | None
    import_missing: bool
    error: str | None = None


class _TransientTTSError(Exception):
    """No TTS backend constructed, and at least one failed at runtime.

    Raised out of the TTS build so its ``Lazy`` caches nothing and the next call
    retries; ``get_tts``/``aget_tts`` turn it into None.
    """


def get_tts() -> TTSBackend | None:
    """Lazy-load the configured TTS backend (Kokoro default; Piper fallback).

    Reads ``config.tts_backend``, tries that adapter first, then falls back to any
    other registered backend whose optional deps are installed. Returns a
    ``TTSBackend`` instance, cached once constructed. Blocks while it loads.

    Fallback semantics: a backend whose optional dependency is simply not
    installed (ImportError) falls back silently — that's the intended
    dep-based selection. A backend that fails at *runtime* (deps present,
    construction/init raised) logs loudly and, if a fallback then succeeds,
    names the configured backend, the error, and the fallback now active. If
    every backend fails and at least one of those failures was a runtime
    error (not just a missing dependency), the failure is NOT cached — a
    later call retries construction, since deps-missing failures never
    change mid-process but runtime failures might be transient.
    """
    try:
        return _tts.get()
    except _TransientTTSError:
        return None


def _build_tts() -> TTSBackend | None:
    """Construct the first TTS backend that loads, in fallback order. Blocks.

    None (cached for good) when every backend's dependency is missing; raises
    ``_TransientTTSError`` (never cached) when any of them failed at runtime.
    """
    from core.voice.tts_registry import TTS_BACKENDS, resolve_backend_order
    from shared.config import AlfredConfig

    selected = AlfredConfig.from_env().tts_backend
    order = resolve_backend_order(selected)
    configured = order[0]
    configured_error: str | None = None
    any_runtime_failure = False

    for name in order:
        module, cls_name, missing_msg = TTS_BACKENDS[name]
        result = _construct_backend(module, cls_name, missing_msg)
        if result.instance is not None:
            if configured_error is not None:
                logger.warning(
                    "Configured TTS backend {!r} failed to initialise ({}); falling back to {!r}",
                    configured,
                    configured_error,
                    name,
                )
            return result.instance
        if not result.import_missing:
            any_runtime_failure = True
            if name == configured:
                configured_error = result.error

    if any_runtime_failure:
        # At least one backend failed with a runtime error rather than a
        # missing dependency — don't cache the failure permanently.
        raise _TransientTTSError
    return None


def _construct_backend(module: str, cls_name: str, missing_msg: str) -> _ConstructResult:
    """Import + instantiate a TTS backend adapter."""
    try:
        mod = importlib.import_module(module)
        instance = cast("TTSBackend", getattr(mod, cls_name)())
        return _ConstructResult(instance, import_missing=False)
    except ImportError:
        logger.warning("{} — {} unavailable", missing_msg, cls_name)
        return _ConstructResult(None, import_missing=True)
    except Exception as exc:
        logger.error("Failed to initialise {}: {}", cls_name, exc)
        return _ConstructResult(None, import_missing=False, error=str(exc))


# Model construction takes 10-40s and must run off the event loop, and a warmup task
# racing a first request must not load the same model twice: core.lazy.Lazy does both
# (see its module docstring for why its lock is never an asyncio.Lock, issue #97).
# STT and TTS share one lock, so they load one at a time.
_voice_load_lock = threading.Lock()
_stt: Lazy[Any] = Lazy(_build_stt, lock=_voice_load_lock)
_tts: Lazy[TTSBackend | None] = Lazy(_build_tts, lock=_voice_load_lock)


async def aget_stt() -> Any:
    """WhisperSTT instance (or None), constructed off the event loop."""
    return await _stt.aget()


async def aget_tts() -> TTSBackend | None:
    """Configured TTS backend instance (or None), constructed off the event loop."""
    try:
        return await _tts.aget()
    except _TransientTTSError:
        return None


async def transcribe_async(stt: Any, audio_bytes: bytes, audio_fmt: str) -> str:
    """Run blocking Whisper transcription in a worker thread."""
    result = await asyncio.to_thread(stt.transcribe, audio_bytes, audio_format=audio_fmt)
    return cast("str", result)


async def synthesize_async(tts: TTSBackend, text: str) -> bytes:
    """Run blocking TTS synthesis in a worker thread."""
    return await asyncio.to_thread(tts.synthesize, text)


def _get_speaker_id_cls() -> Any:
    """Lazy-import SpeakerID class (requires voice extra deps at embed time)."""
    from core.voice.speaker_id import SpeakerID

    return SpeakerID


_FAILED: object = object()  # sentinel for an import that already failed
# The shared SpeakerID, or _FAILED once its import has failed; None until first asked.
_speaker_id: Any = None


async def aget_speaker_id(redis: Any) -> Any | None:
    """Shared SpeakerID singleton, or None if the voice extra is unavailable.

    Two concurrent first callers must not each build their own SpeakerID (each would
    later load its own ECAPA model), and no lock is needed to stop them: nothing here
    awaits, so the cache check, the construction and the cache write run as one step
    no other task on the loop can interleave. (Construction is cheap; the model loads
    later, off the loop, inside SpeakerID.) Keep it await-free. A construction that
    ever has to leave the loop must go through ``core.lazy.Lazy``, never a
    module-level asyncio.Lock (issue #97).
    """
    global _speaker_id
    if _speaker_id is None:
        try:
            _speaker_id = _get_speaker_id_cls()(redis)
        except ImportError:
            logger.warning("speechbrain not installed — speaker ID disabled")
            _speaker_id = _FAILED
    return None if _speaker_id is _FAILED else _speaker_id
