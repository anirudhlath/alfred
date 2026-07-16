"""PyAV decode helper (voice extra)."""

import pytest

av = pytest.importorskip("av")

from core.channels.satellite.audio import pcm_to_wav  # noqa: E402
from core.voice.audio import decode_to_pcm16k  # noqa: E402


def test_decode_wav_to_pcm16k() -> None:
    # 0.5s of silence @ 22050 Hz mono s16 — decode must resample to 16 kHz
    src = pcm_to_wav(b"\x00\x00" * 11025, rate=22050)
    pcm = decode_to_pcm16k(src)
    n_samples = len(pcm) // 2
    assert abs(n_samples - 8000) < 160  # ~0.5s @ 16kHz, resampler edge tolerance
