"""PiperTTS — text-to-speech using Piper (local ONNX inference)."""

from __future__ import annotations

import io
import wave
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from shared.traced import traced

if TYPE_CHECKING:
    from piper import PiperVoice
    from piper.config import SynthesisConfig

# Silence between sentences (samples at 22050 Hz, 16-bit mono)
_SENTENCE_PAUSE_MS = 250


class PiperTTS:
    """Text-to-speech using Piper (local, no cloud dependency).

    Uses ONNX voice models for fast CPU inference via the piper-tts Python API.
    Synthesis parameters are tuned for natural, brisk speech.
    """

    DEFAULT_VOICE = "en_GB-alan-medium"
    DEFAULT_MODEL_DIR = Path(__file__).resolve().parent / "models"

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        model_dir: Path = DEFAULT_MODEL_DIR,
        length_scale: float = 0.75,
        noise_scale: float = 0.667,
        noise_w: float = 0.3,
    ) -> None:
        from piper import PiperVoice as _PiperVoice
        from piper.config import SynthesisConfig as _SynthesisConfig

        model_path = model_dir / f"{voice}.onnx"
        if not model_path.exists():
            raise FileNotFoundError(
                f"Piper voice model not found: {model_path}. "
                f"Download from https://huggingface.co/rhasspy/piper-voices"
            )
        self._voice: PiperVoice = _PiperVoice.load(str(model_path))
        self._sample_rate: int = 22050
        self._syn_config: SynthesisConfig = _SynthesisConfig(
            length_scale=length_scale,
            noise_scale=noise_scale,
            noise_w_scale=noise_w,
        )
        logger.info(
            "Loaded Piper TTS voice: {} (length={}, noise={}, noise_w={})",
            voice,
            length_scale,
            noise_scale,
            noise_w,
        )

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
