"""Tests for Whisper STT wrapper."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

from core.voice.stt import WhisperSTT


def test_stt_instantiation() -> None:
    """WhisperSTT can be created with default model."""
    stt = WhisperSTT.__new__(WhisperSTT)
    assert hasattr(stt, "transcribe")
    assert hasattr(stt, "transcribe_file")


def test_stt_model_name() -> None:
    """Default model is large-v3-turbo."""
    assert WhisperSTT.DEFAULT_MODEL == "large-v3-turbo"


@dataclass
class _MockSegment:
    text: str


@dataclass
class _MockInfo:
    duration: float = 2.5
    language: str = "en"
    language_probability: float = 0.99


def test_transcribe_file_joins_segments() -> None:
    """transcribe_file joins segment text with spaces."""
    stt = WhisperSTT.__new__(WhisperSTT)
    mock_model = MagicMock()
    segments = [_MockSegment(text="Hello"), _MockSegment(text="world")]
    mock_model.transcribe.return_value = (segments, _MockInfo())
    stt._model = mock_model

    result = stt.transcribe_file("/tmp/test.wav", language="en")
    assert result == "Hello world"
    mock_model.transcribe.assert_called_once_with("/tmp/test.wav", language="en", beam_size=5)


def test_transcribe_writes_temp_file() -> None:
    """transcribe writes audio bytes to a temp file then calls transcribe_file."""
    stt = WhisperSTT.__new__(WhisperSTT)
    mock_model = MagicMock()
    segments = [_MockSegment(text="Test")]
    mock_model.transcribe.return_value = (segments, _MockInfo())
    stt._model = mock_model

    result = stt.transcribe(b"fake audio bytes")
    assert result == "Test"
    assert mock_model.transcribe.call_count == 1


def test_transcribe_empty_audio() -> None:
    """transcribe handles empty segments gracefully."""
    stt = WhisperSTT.__new__(WhisperSTT)
    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([], _MockInfo(duration=0.0))
    stt._model = mock_model

    result = stt.transcribe(b"empty")
    assert result == ""
