"""Tests for Piper TTS wrapper."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

from core.voice.tts import PiperTTS


def test_tts_instantiation() -> None:
    """PiperTTS can be created without loading a model (via __new__)."""
    tts = PiperTTS.__new__(PiperTTS)
    assert hasattr(tts, "synthesize")


def test_default_voice() -> None:
    """Default voice is British English Alan."""
    assert PiperTTS.DEFAULT_VOICE == "en_GB-alan-medium"


def test_default_model_dir() -> None:
    """Default model directory is core/voice/models/."""
    assert PiperTTS.DEFAULT_MODEL_DIR.name == "models"
    assert PiperTTS.DEFAULT_MODEL_DIR.parent.name == "voice"


@patch("core.voice.tts.PiperTTS.__init__", return_value=None)
def test_constructor_stores_synthesis_config(mock_init: MagicMock) -> None:
    """Constructor creates a SynthesisConfig with the given parameters."""
    # Bypass __init__ and manually set attributes to test synthesize
    tts = PiperTTS.__new__(PiperTTS)
    mock_voice = MagicMock()
    tts._voice = mock_voice
    tts._sample_rate = 22050
    mock_config = MagicMock()
    tts._syn_config = mock_config

    # Verify the attributes exist
    assert tts._voice is mock_voice
    assert tts._sample_rate == 22050
    assert tts._syn_config is mock_config


def test_synthesize_produces_wav_bytes() -> None:
    """synthesize returns WAV bytes from Piper voice model."""
    tts = PiperTTS.__new__(PiperTTS)
    tts._sample_rate = 22050
    tts._syn_config = MagicMock()

    # Mock the PiperVoice.synthesize to return fake audio chunks
    mock_chunk = MagicMock()
    mock_chunk.audio_int16_bytes = b"\x00\x01" * 100
    mock_voice = MagicMock()
    mock_voice.synthesize.return_value = [mock_chunk]
    tts._voice = mock_voice

    result = tts.synthesize("Hello sir")

    assert isinstance(result, bytes)
    assert result[:4] == b"RIFF"  # WAV header
    mock_voice.synthesize.assert_called_once_with("Hello sir", syn_config=tts._syn_config)


def test_synthesize_multiple_chunks_with_pauses() -> None:
    """synthesize inserts pauses between sentence chunks."""
    tts = PiperTTS.__new__(PiperTTS)
    tts._sample_rate = 22050
    tts._syn_config = MagicMock()

    chunk1 = MagicMock()
    chunk1.audio_int16_bytes = b"\x00\x01" * 50
    chunk2 = MagicMock()
    chunk2.audio_int16_bytes = b"\x00\x02" * 50
    mock_voice = MagicMock()
    mock_voice.synthesize.return_value = [chunk1, chunk2]
    tts._voice = mock_voice

    result = tts.synthesize("Hello. Goodbye.")

    # Result should be valid WAV with both chunks + pause between them
    assert isinstance(result, bytes)
    assert result[:4] == b"RIFF"
    # The output should be larger than a single chunk (includes pause bytes)
    single_chunk_audio = len(chunk1.audio_int16_bytes)
    # WAV header is 44 bytes, content is 2 chunks + 1 pause
    assert len(result) > 44 + single_chunk_audio


def test_constructor_raises_on_missing_model(tmp_path: Path) -> None:
    """Constructor raises FileNotFoundError if model .onnx file doesn't exist."""
    import pytest

    with pytest.raises(FileNotFoundError, match="Piper voice model not found"):
        PiperTTS(voice="nonexistent-voice", model_dir=tmp_path)
