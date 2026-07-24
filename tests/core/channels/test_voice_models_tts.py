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
    # Every backend failed with ImportError (deps won't appear mid-process) —
    # the failure is cached permanently.
    assert vm._lazy_cache["tts"] is vm._FAILED
    _reset()


def test_get_tts_runtime_failure_of_selected_falls_back_with_warning(
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    """Configured backend fails at runtime (deps present, init raised) — the
    fallback must still be used, and a loud warning must name the configured
    backend, the error, and the fallback now active."""
    _reset()
    monkeypatch.setenv("ALFRED_TTS_BACKEND", "kokoro")
    piper = object()

    def fake_import(name: str):  # type: ignore[no-untyped-def]
        mod = MagicMock()
        if name == "core.voice.tts_kokoro":
            mod.KokoroTTS = MagicMock(side_effect=RuntimeError("onnx init failed"))
        else:
            mod.PiperTTS = MagicMock(return_value=piper)
        return mod

    mock_logger = MagicMock()
    monkeypatch.setattr(vm, "logger", mock_logger)

    with patch("importlib.import_module", side_effect=fake_import):
        assert vm.get_tts() is piper

    assert vm._lazy_cache["tts"] is piper
    warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
    assert any("kokoro" in c and "piper" in c and "onnx init failed" in c for c in warning_calls)
    _reset()


def test_get_tts_all_runtime_fail_returns_none_and_not_cached(
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    """When every backend fails with a runtime error (not a missing dep), the
    failure must NOT be cached as _FAILED — a later call retries construction."""
    _reset()
    monkeypatch.setenv("ALFRED_TTS_BACKEND", "kokoro")
    calls = {"count": 0}

    def fake_construct(module: str, cls_name: str, missing_msg: str):  # type: ignore[no-untyped-def]
        calls["count"] += 1
        return vm._ConstructResult(None, import_missing=False, error="boom")

    monkeypatch.setattr(vm, "_construct_backend", fake_construct)

    assert vm.get_tts() is None
    assert "tts" not in vm._lazy_cache
    first_call_count = calls["count"]
    assert first_call_count > 0

    assert vm.get_tts() is None  # second call retries construction, not short-circuited
    assert calls["count"] > first_call_count
    assert "tts" not in vm._lazy_cache
    _reset()
