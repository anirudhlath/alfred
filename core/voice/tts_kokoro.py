"""KokoroTTS — neural text-to-speech via Kokoro-82M (kokoro-onnx, local ONNX)."""

from __future__ import annotations

import io
import os
import wave
from typing import TYPE_CHECKING

import numpy as np
from loguru import logger

from core.voice.hf_models import ensure_model
from core.voice.tts_backend import TTSBackend
from shared.config import AlfredConfig
from shared.traced import traced

if TYPE_CHECKING:
    from kokoro_onnx import EspeakConfig, Kokoro  # type: ignore[attr-defined]

# Pinned HF source (fastrtc/kokoro-onnx: kokoro-v1.0.onnx + voices-v1.0.bin, MIT).
_KOKORO_REPO = "fastrtc/kokoro-onnx"
_KOKORO_REVISION = "8d07950c9b6c87ce6809e9bba7bd494336217c2a"
_MODEL_FILE = "kokoro-v1.0.onnx"
_VOICES_FILE = "voices-v1.0.bin"

_PROVIDER_BY_SETTING = {
    "cpu": "CPUExecutionProvider",
    "cuda": "CUDAExecutionProvider",
    "coreml": "CoreMLExecutionProvider",
}


def _build_espeak_config() -> EspeakConfig:
    """Explicit espeak lib/data paths from espeakng_loader.

    Makes phonemization deterministic and immune to ambient PHONEMIZER_ESPEAK_* /
    ESPEAK_DATA_PATH env vars — the root cause of the 'phontab: No such file or
    directory' failure seen during the spike.
    """
    import espeakng_loader
    from kokoro_onnx import EspeakConfig as _EspeakConfig  # type: ignore[attr-defined]

    return _EspeakConfig(
        lib_path=espeakng_loader.get_library_path(),
        data_path=espeakng_loader.get_data_path(),
    )


def _resolve_provider(setting: str) -> str:
    """Map a provider setting to a concrete ONNX execution provider.

    'auto' picks CUDA when onnxruntime exposes it (the RTX 4090 deployment), else
    CPU. kokoro-onnx's own gpu auto-detect is unreliable, so we pin the provider
    via the ONNX_PROVIDER env var it honours.
    """
    if setting in _PROVIDER_BY_SETTING:
        return _PROVIDER_BY_SETTING[setting]
    if setting != "auto":
        logger.warning(
            "Unknown Kokoro ONNX provider setting {!r} — auto-resolving instead", setting
        )
    import onnxruntime as ort

    if "CUDAExecutionProvider" in ort.get_available_providers():
        return "CUDAExecutionProvider"
    return "CPUExecutionProvider"


class KokoroTTS(TTSBackend):
    """Neural TTS using Kokoro-82M via kokoro-onnx.

    One ONNX graph on macOS (CPU EP) and the RTX 4090 (CUDA EP). Auto-downloads the
    model from the HF Hub on first use. Output is 16-bit PCM mono WAV bytes.
    """

    def __init__(
        self,
        voice: str | None = None,
        speed: float | None = None,
        provider: str | None = None,
    ) -> None:
        from kokoro_onnx import Kokoro as _Kokoro

        config = AlfredConfig.from_env()
        self._voice: str = voice if voice is not None else config.kokoro_voice
        self._speed: float = speed if speed is not None else config.kokoro_speed
        provider_setting = provider if provider is not None else config.kokoro_onnx_provider

        model_path = ensure_model(_KOKORO_REPO, _MODEL_FILE, _KOKORO_REVISION)
        voices_path = ensure_model(_KOKORO_REPO, _VOICES_FILE, _KOKORO_REVISION)
        # Process-global env var, not a constructor arg — kokoro-onnx 0.5 reads
        # ONNX_PROVIDER itself at Kokoro() construction time, so this must be set
        # before instantiating below.
        os.environ["ONNX_PROVIDER"] = _resolve_provider(provider_setting)

        self._kokoro: Kokoro = _Kokoro(
            str(model_path), str(voices_path), espeak_config=_build_espeak_config()
        )
        logger.info(
            "Loaded Kokoro TTS (voice={}, speed={}, provider={})",
            self._voice,
            self._speed,
            os.environ["ONNX_PROVIDER"],
        )

    @traced(name="voice.tts.synthesize")
    def synthesize(self, text: str) -> bytes:
        """Synthesize ``text`` to 16-bit PCM mono WAV bytes."""
        samples, sample_rate = self._kokoro.create(
            text, voice=self._voice, speed=self._speed, lang="en-us"
        )
        pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(sample_rate)
            wf.writeframes(pcm)
        return buf.getvalue()
