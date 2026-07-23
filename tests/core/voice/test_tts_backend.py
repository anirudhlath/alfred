"""Tests for the TTSBackend ABC port."""

from __future__ import annotations

import pytest

from core.voice.tts_backend import TTSBackend


def test_cannot_instantiate_abstract() -> None:
    with pytest.raises(TypeError):
        TTSBackend()  # type: ignore[abstract]


def test_subclass_missing_method_is_abstract() -> None:
    class Bad(TTSBackend):
        pass

    with pytest.raises(TypeError):
        Bad()  # type: ignore[abstract]


def test_concrete_subclass_works() -> None:
    class Dummy(TTSBackend):
        def synthesize(self, text: str) -> bytes:
            return b"RIFF" + text.encode()

    d = Dummy()
    assert isinstance(d, TTSBackend)
    assert d.synthesize("hi") == b"RIFFhi"
