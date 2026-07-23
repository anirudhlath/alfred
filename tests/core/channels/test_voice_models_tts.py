"""Tests for TTS backend selection + fallback in voice_models.get_tts()."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import core.channels.voice_models as vm


def _reset() -> None:
    vm._lazy_cache.clear()


def test_get_tts_selects_configured_backend(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _reset()
    monkeypatch.setenv("ALFRED_TTS_BACKEND", "piper")
    sentinel = object()

    def fake_import(name: str):  # type: ignore[no-untyped-def]
        mod = MagicMock()
        if name == "core.voice.tts":
            mod.PiperTTS = MagicMock(return_value=sentinel)
        return mod

    with patch("importlib.import_module", side_effect=fake_import):
        assert vm.get_tts() is sentinel
    _reset()


def test_get_tts_falls_back_when_selected_missing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _reset()
    monkeypatch.setenv("ALFRED_TTS_BACKEND", "kokoro")
    piper = object()

    def fake_import(name: str):  # type: ignore[no-untyped-def]
        if name == "core.voice.tts_kokoro":
            raise ImportError("no kokoro")
        mod = MagicMock()
        mod.PiperTTS = MagicMock(return_value=piper)
        return mod

    with patch("importlib.import_module", side_effect=fake_import):
        assert vm.get_tts() is piper
    _reset()


def test_get_tts_all_fail_returns_none(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _reset()
    monkeypatch.setenv("ALFRED_TTS_BACKEND", "kokoro")
    with patch("importlib.import_module", side_effect=ImportError("nope")):
        assert vm.get_tts() is None
    assert vm._lazy_cache["tts"] is vm._FAILED
    _reset()
