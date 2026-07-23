"""PiperTTS — text-to-speech using Piper (local ONNX inference), HF-Hub model."""

from __future__ import annotations

import io
import wave
from typing import TYPE_CHECKING

from loguru import logger

from core.voice.hf_models import ensure_model
from core.voice.tts_backend import TTSBackend
from shared.traced import traced

if TYPE_CHECKING:
    from pathlib import Path

    from piper import PiperVoice
    from piper.config import SynthesisConfig

# Silence between sentences (samples at 22050 Hz, 16-bit mono)
_SENTENCE_PAUSE_MS = 250

# Pinned HF source for Piper voices.
_PIPER_REPO = "rhasspy/piper-voices"
_PIPER_REVISION = "5b44ec7bab7c5822cfec48fbd5aa99db71a823d6"


def _voice_path(voice: str) -> str:
    """Map a Piper voice name to its repo-relative path (no extension).

    e.g. en_GB-alan-medium → en/en_GB/alan/medium/en_GB-alan-medium
    """
    parts = voice.split("-")
    lang_region = parts[0]  # en_GB
    lang = lang_region.split("_")[0]  # en
    speaker = parts[1]  # alan
    quality = parts[2] if len(parts) > 2 else "medium"
    return f"{lang}/{lang_region}/{speaker}/{quality}/{voice}"


def _download_model(voice: str) -> Path:
    """Fetch the Piper ONNX model + config from the HF Hub; return the .onnx path.

    Both files land in the same HF snapshot dir, so PiperVoice.load finds the
    config alongside the model.
    """
    base = _voice_path(voice)
    ensure_model(_PIPER_REPO, f"{base}.onnx.json", _PIPER_REVISION)  # config alongside
    return ensure_model(_PIPER_REPO, f"{base}.onnx", _PIPER_REVISION)


class PiperTTS(TTSBackend):
    """Text-to-speech using Piper (local, no cloud dependency).

    Fallback backend behind the TTSBackend seam. Auto-downloads voice models from
    the HF Hub on first use.
    """

    DEFAULT_VOICE = "en_GB-alan-medium"

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        length_scale: float = 0.75,
        noise_scale: float = 0.667,
        noise_w: float = 0.3,
    ) -> None:
        from piper import PiperVoice as _PiperVoice
        from piper.config import SynthesisConfig as _SynthesisConfig

        model_path = _download_model(voice)
        self._voice: PiperVoice = _PiperVoice.load(str(model_path))
        self._sample_rate: int = 22050
        self._syn_config: SynthesisConfig = _SynthesisConfig(
            length_scale=length_scale,
            noise_scale=noise_scale,
            noise_w_scale=noise_w,
        )
        logger.info("Loaded Piper TTS voice: {}", voice)

    @traced(name="voice.tts.synthesize")
    def synthesize(self, text: str) -> bytes:
        """Synthesize text to WAV audio bytes."""
        chunks = list(self._voice.synthesize(text, syn_config=self._syn_config))
        pause = b"\x00\x00" * int(self._sample_rate * _SENTENCE_PAUSE_MS / 1000)

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(self._sample_rate)
            for i, chunk in enumerate(chunks):
                wf.writeframes(chunk.audio_int16_bytes)
                if i < len(chunks) - 1:
                    wf.writeframes(pause)

        return buf.getvalue()
