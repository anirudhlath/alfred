"""Cross-platform espeak phonemization smoke test.

Exercises the espeak wiring KokoroTTS uses (explicit EspeakConfig from
espeakng_loader). Guards the 'phontab: No such file or directory' regression on
macOS + Linux. Does NOT download the 325 MB ONNX model.
"""

from __future__ import annotations

import pytest


def test_espeak_phonemization() -> None:
    pytest.importorskip("kokoro_onnx", reason="voice extra not installed")
    from kokoro_onnx.tokenizer import Tokenizer

    from core.voice.tts_kokoro import _build_espeak_config

    phonemes = Tokenizer(_build_espeak_config()).phonemize("Hello world, sir.", "en-us")
    assert phonemes.strip(), "espeak produced no phonemes"
