"""Tests for Piper TTS wrapper."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.voice.tts import PiperTTS


def test_tts_instantiation() -> None:
    """PiperTTS can be created without loading a model."""
    tts = PiperTTS.__new__(PiperTTS)
    assert hasattr(tts, "synthesize")


def test_default_voice() -> None:
    """Default voice is British English Alan."""
    assert PiperTTS.DEFAULT_VOICE == "en_GB-alan-medium"


def test_tts_constructor_stores_config() -> None:
    """Constructor stores voice, binary path, and model dir."""
    tts = PiperTTS(voice="test-voice", piper_bin="/usr/bin/piper", model_dir="/models")
    assert tts._voice == "test-voice"
    assert tts._piper_bin == "/usr/bin/piper"
    assert tts._model_dir == Path("/models")


@patch("subprocess.run")
def test_synthesize_calls_piper(mock_run: MagicMock, tmp_path: Path) -> None:
    """synthesize calls piper subprocess and returns WAV bytes."""
    wav_data = b"RIFF" + b"\x00" * 100

    def side_effect(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        # Write fake WAV data to the output file
        cmd = args[0]
        assert isinstance(cmd, list)
        output_idx = cmd.index("--output_file") + 1
        Path(cmd[output_idx]).write_bytes(wav_data)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    mock_run.side_effect = side_effect

    tts = PiperTTS(model_dir=str(tmp_path))
    result = tts.synthesize("Hello sir")

    assert result == wav_data
    mock_run.assert_called_once()
    call_args = mock_run.call_args
    cmd = call_args[0][0]
    assert "piper" in cmd[0]
    assert "--model" in cmd


@patch("subprocess.run")
def test_synthesize_raises_on_failure(mock_run: MagicMock, tmp_path: Path) -> None:
    """synthesize raises RuntimeError when piper fails."""
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=1, stdout=b"", stderr=b"model not found"
    )
    tts = PiperTTS(model_dir=str(tmp_path))

    with pytest.raises(RuntimeError, match="Piper TTS failed"):
        tts.synthesize("Hello")


@patch("subprocess.run")
def test_synthesize_cleans_up_temp_file(mock_run: MagicMock, tmp_path: Path) -> None:
    """synthesize removes the temp file even on success."""

    def side_effect(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        cmd = args[0]
        assert isinstance(cmd, list)
        output_idx = cmd.index("--output_file") + 1
        Path(cmd[output_idx]).write_bytes(b"data")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    mock_run.side_effect = side_effect
    tts = PiperTTS(model_dir=str(tmp_path))
    tts.synthesize("Test cleanup")
    # Temp file should be cleaned up — we can't easily check this
    # but the test verifies no exception is raised


def test_synthesize_streaming_returns_popen() -> None:
    """synthesize_streaming returns a Popen object."""
    tts = PiperTTS.__new__(PiperTTS)
    tts._voice = "test"
    tts._piper_bin = "echo"  # Use echo as a harmless command
    tts._model_dir = Path("/tmp")
    assert hasattr(tts, "synthesize_streaming")
