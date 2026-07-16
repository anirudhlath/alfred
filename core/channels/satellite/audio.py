"""PCM/WAV framing helpers for the satellite bridge."""

from __future__ import annotations

import io
import wave


def pcm_to_wav(pcm: bytes, rate: int = 16000, width: int = 2, channels: int = 1) -> bytes:
    """Wrap raw PCM in a WAV container (for Whisper/file interfaces)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return buf.getvalue()


def wav_to_pcm(wav_bytes: bytes) -> tuple[bytes, int, int, int]:
    """Extract (pcm, rate, width, channels) from a WAV container (for Wyoming playback)."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        return (
            wav.readframes(wav.getnframes()),
            wav.getframerate(),
            wav.getsampwidth(),
            wav.getnchannels(),
        )
