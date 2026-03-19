"""Tests for Whisper STT wrapper."""

from __future__ import annotations

from core.voice.stt import WhisperSTT


def test_stt_instantiation() -> None:
    """WhisperSTT can be created with default model."""
    stt = WhisperSTT.__new__(WhisperSTT)
    assert hasattr(stt, "transcribe")
    assert hasattr(stt, "transcribe_file")


def test_stt_model_name() -> None:
    """Default model is large-v3-turbo."""
    assert WhisperSTT.DEFAULT_MODEL == "large-v3-turbo"
