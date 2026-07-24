"""Tests for TTS backend config fields."""

from __future__ import annotations

from shared.config import AlfredConfig


def test_tts_defaults() -> None:
    cfg = AlfredConfig()
    assert cfg.tts_backend == "kokoro"
    assert cfg.kokoro_voice == "am_michael"
    assert cfg.kokoro_speed == 1.0
    assert cfg.kokoro_onnx_provider == "auto"


def test_tts_from_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ALFRED_TTS_BACKEND", "piper")
    monkeypatch.setenv("KOKORO_VOICE", "bm_george")
    monkeypatch.setenv("KOKORO_SPEED", "1.2")
    monkeypatch.setenv("KOKORO_ONNX_PROVIDER", "cpu")
    cfg = AlfredConfig.from_env()
    assert cfg.tts_backend == "piper"
    assert cfg.kokoro_voice == "bm_george"
    assert cfg.kokoro_speed == 1.2
    assert cfg.kokoro_onnx_provider == "cpu"
