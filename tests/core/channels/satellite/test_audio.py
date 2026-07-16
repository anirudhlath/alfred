"""WAV framing helpers."""

from core.channels.satellite.audio import pcm_to_wav, wav_to_pcm


def test_pcm_wav_roundtrip() -> None:
    pcm = bytes(range(256)) * 8  # 2048 bytes of arbitrary s16le
    wav = pcm_to_wav(pcm, rate=16000)
    out, rate, width, channels = wav_to_pcm(wav)
    assert (out, rate, width, channels) == (pcm, 16000, 2, 1)


def test_pcm_to_wav_has_riff_header() -> None:
    assert pcm_to_wav(b"\x00\x00" * 160)[:4] == b"RIFF"
