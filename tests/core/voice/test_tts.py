"""Tests for Piper TTS wrapper."""

from __future__ import annotations

from core.voice.tts import PiperTTS


def test_tts_instantiation() -> None:
    """PiperTTS can be created without loading a model."""
    tts = PiperTTS.__new__(PiperTTS)
    assert hasattr(tts, "synthesize")


def test_default_voice() -> None:
    """Default voice is British English Alan."""
    assert PiperTTS.DEFAULT_VOICE == "en_GB-alan-medium"
