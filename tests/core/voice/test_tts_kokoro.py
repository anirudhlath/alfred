"""Tests for the Kokoro TTS adapter."""

from __future__ import annotations

import io
import struct
import wave
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from core.voice.tts_backend import TTSBackend
from core.voice.tts_kokoro import (
    _KOKORO_REPO,
    _MODEL_FILE,
    _VOICES_FILE,
    KokoroTTS,
    _resolve_provider,
)


def test_is_ttsbackend_subclass() -> None:
    assert issubclass(KokoroTTS, TTSBackend)


def test_resolve_provider_explicit() -> None:
    assert _resolve_provider("cpu") == "CPUExecutionProvider"
    assert _resolve_provider("cuda") == "CUDAExecutionProvider"
    assert _resolve_provider("coreml") == "CoreMLExecutionProvider"


def test_resolve_provider_auto_prefers_cuda() -> None:
    with patch(
        "onnxruntime.get_available_providers",
        return_value=["CUDAExecutionProvider", "CPUExecutionProvider"],
    ):
        assert _resolve_provider("auto") == "CUDAExecutionProvider"


def test_resolve_provider_auto_falls_back_to_cpu() -> None:
    with patch("onnxruntime.get_available_providers", return_value=["CPUExecutionProvider"]):
        assert _resolve_provider("auto") == "CPUExecutionProvider"


def test_resolve_provider_unknown_setting_warns_and_auto_resolves() -> None:
    """An unrecognised provider setting must not raise — it logs a warning and
    falls through to the same auto-resolution logic as 'auto'."""
    import core.voice.tts_kokoro as tts_kokoro_mod

    mock_logger = MagicMock()
    with (
        patch.object(tts_kokoro_mod, "logger", mock_logger),
        patch("onnxruntime.get_available_providers", return_value=["CPUExecutionProvider"]),
    ):
        assert _resolve_provider("not-a-real-provider") == "CPUExecutionProvider"

    mock_logger.warning.assert_called_once()
    assert "not-a-real-provider" in str(mock_logger.warning.call_args)


def test_repo_constant() -> None:
    assert _KOKORO_REPO == "fastrtc/kokoro-onnx"
    assert _MODEL_FILE == "kokoro-v1.0.onnx"
    assert _VOICES_FILE == "voices-v1.0.bin"


def test_synthesize_wraps_wav() -> None:
    tts = KokoroTTS.__new__(KokoroTTS)  # bypass model load
    tts._voice = "am_michael"
    tts._speed = 1.0
    mock_k = MagicMock()
    # Include out-of-range samples (2.0, -2.0) — kokoro-onnx can emit values
    # outside [-1, 1] and the PCM conversion must clip/saturate, not wrap.
    mock_k.create.return_value = (
        np.array([0.0, 0.5, -0.5, 1.0, 2.0, -2.0], dtype=np.float32),
        24000,
    )
    tts._kokoro = mock_k

    result = tts.synthesize("Hello sir")

    assert isinstance(result, bytes)
    assert result[:4] == b"RIFF"
    mock_k.create.assert_called_once_with("Hello sir", voice="am_michael", speed=1.0, lang="en-us")

    with wave.open(io.BytesIO(result), "rb") as wf:
        n_frames = wf.getnframes()
        frames = wf.readframes(n_frames)
    samples = struct.unpack(f"<{n_frames}h", frames)

    # 2.0 and -2.0 must saturate at +/-32767, not wrap around int16.
    assert samples[4] == 32767
    assert samples[5] == -32767
    assert max(samples) <= 32767
    assert min(samples) >= -32767


def test_real_synthesis_end_to_end() -> None:
    pytest.importorskip("kokoro_onnx", reason="voice extra not installed")
    if "CI" in __import__("os").environ:
        pytest.skip("skips 353 MB model download in CI")
    wav = KokoroTTS().synthesize("Good evening, sir.")
    assert wav[:4] == b"RIFF"
    assert len(wav) > 44
