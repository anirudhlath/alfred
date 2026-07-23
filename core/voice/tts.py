"""PiperTTS — text-to-speech using Piper (local ONNX inference)."""

from __future__ import annotations

import io
import urllib.request
import wave
from typing import TYPE_CHECKING

from loguru import logger

from shared.traced import traced

if TYPE_CHECKING:
    from pathlib import Path

    from piper import PiperVoice
    from piper.config import SynthesisConfig

# Silence between sentences (samples at 22050 Hz, 16-bit mono)
_SENTENCE_PAUSE_MS = 250

_HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


def _voice_url(voice: str) -> str:
    """Build HuggingFace URL for a Piper voice model.

    Voice names follow the pattern: lang_REGION-speaker-quality
    e.g. en_GB-alan-medium → en/en_GB/alan/medium/en_GB-alan-medium
    """
    parts = voice.split("-")
    lang_region = parts[0]  # en_GB
    lang = lang_region.split("_")[0]  # en
    speaker = parts[1]  # alan
    quality = parts[2] if len(parts) > 2 else "medium"
    return f"{_HF_BASE}/{lang}/{lang_region}/{speaker}/{quality}/{voice}"


def _default_model_dir() -> Path:
    from shared.config import models_root

    return models_root() / "piper"


def _download_model(voice: str, model_dir: Path) -> None:
    """Download Piper ONNX model + config from HuggingFace."""
    model_dir.mkdir(parents=True, exist_ok=True)
    base_url = _voice_url(voice)

    for suffix in (".onnx", ".onnx.json"):
        url = f"{base_url}{suffix}"
        dest = model_dir / f"{voice}{suffix}"
        if dest.exists():
            continue
        logger.info("Downloading Piper voice model: {} → {}", url, dest)
        urllib.request.urlretrieve(url, dest)
        logger.info("Downloaded {} ({:.1f} MB)", dest.name, dest.stat().st_size / 1e6)


class PiperTTS:
    """Text-to-speech using Piper (local, no cloud dependency).

    Uses ONNX voice models for fast CPU inference via the piper-tts Python API.
    Synthesis parameters are tuned for natural, brisk speech.
    Auto-downloads models from HuggingFace on first use.
    """

    DEFAULT_VOICE = "en_GB-alan-medium"

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        model_dir: Path | None = None,
        length_scale: float = 0.75,
        noise_scale: float = 0.667,
        noise_w: float = 0.3,
    ) -> None:
        from piper import PiperVoice as _PiperVoice
        from piper.config import SynthesisConfig as _SynthesisConfig

        model_dir = model_dir if model_dir is not None else _default_model_dir()
        model_path = model_dir / f"{voice}.onnx"
        if not model_path.exists():
            logger.info("Voice model not found — downloading {}", voice)
            _download_model(voice, model_dir)
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
