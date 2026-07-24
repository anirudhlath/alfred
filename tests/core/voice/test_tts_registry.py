"""Tests for the TTS backend registry."""

from __future__ import annotations

from core.voice.tts_registry import (
    DEFAULT_TTS_BACKEND,
    TTS_BACKENDS,
    resolve_backend_order,
)


def test_registry_entries() -> None:
    assert set(TTS_BACKENDS) == {"kokoro", "piper"}
    assert TTS_BACKENDS["kokoro"] == (
        "core.voice.tts_kokoro",
        "KokoroTTS",
        "kokoro-onnx not installed",
    )
    assert TTS_BACKENDS["piper"] == ("core.voice.tts", "PiperTTS", "piper-tts not installed")
    assert DEFAULT_TTS_BACKEND == "kokoro"


def test_resolve_order_selected_first() -> None:
    assert resolve_backend_order("kokoro") == ["kokoro", "piper"]
    assert resolve_backend_order("piper") == ["piper", "kokoro"]


def test_resolve_order_unknown_uses_default() -> None:
    order = resolve_backend_order("bogus")
    assert order[0] == DEFAULT_TTS_BACKEND
    assert set(order) == {"kokoro", "piper"}
