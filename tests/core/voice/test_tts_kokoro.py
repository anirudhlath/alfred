"""Tests for the Kokoro TTS adapter."""

from __future__ import annotations

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


def test_repo_constant() -> None:
    assert _KOKORO_REPO == "fastrtc/kokoro-onnx"
    assert _MODEL_FILE == "kokoro-v1.0.onnx"
    assert _VOICES_FILE == "voices-v1.0.bin"


def test_synthesize_wraps_wav() -> None:
    tts = KokoroTTS.__new__(KokoroTTS)  # bypass model load
    tts._voice = "am_michael"
    tts._speed = 1.0
    mock_k = MagicMock()
    mock_k.create.return_value = (np.array([0.0, 0.5, -0.5, 1.0], dtype=np.float32), 24000)
    tts._kokoro = mock_k

    result = tts.synthesize("Hello sir")

    assert isinstance(result, bytes)
    assert result[:4] == b"RIFF"
    mock_k.create.assert_called_once_with("Hello sir", voice="am_michael", speed=1.0, lang="en-us")


def test_real_synthesis_end_to_end() -> None:
    pytest.importorskip("kokoro_onnx", reason="voice extra not installed")
    if "CI" in __import__("os").environ:
        pytest.skip("skips 353 MB model download in CI")
    wav = KokoroTTS().synthesize("Good evening, sir.")
    assert wav[:4] == b"RIFF"
    assert len(wav) > 44
