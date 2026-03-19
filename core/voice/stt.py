"""WhisperSTT — speech-to-text using faster-whisper (local, GPU-accelerated)."""

from __future__ import annotations

import tempfile
from typing import TYPE_CHECKING

from loguru import logger

from shared.traced import traced

if TYPE_CHECKING:
    from faster_whisper import WhisperModel


class WhisperSTT:
    """Speech-to-text using faster-whisper (CTranslate2 backend).

    Runs entirely locally on GPU or CPU. No cloud dependency.
    """

    DEFAULT_MODEL = "large-v3-turbo"

    def __init__(
        self,
        model_size: str = DEFAULT_MODEL,
        device: str = "auto",
        compute_type: str = "float16",
    ) -> None:
        from faster_whisper import WhisperModel as _WhisperModel

        self._model: WhisperModel = _WhisperModel(
            model_size, device=device, compute_type=compute_type
        )
        logger.info("Loaded Whisper model: {} (device={})", model_size, device)

    @traced(name="voice.stt.transcribe")
    def transcribe(self, audio_bytes: bytes, language: str = "en") -> str:
        """Transcribe audio bytes to text.

        Args:
            audio_bytes: Raw audio data (WAV, MP3, OGG, etc.)
            language: Language code for transcription.

        Returns:
            Transcribed text string.
        """
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
            tmp.write(audio_bytes)
            tmp.flush()
            return self.transcribe_file(tmp.name, language=language)

    @traced(name="voice.stt.transcribe_file")
    def transcribe_file(self, file_path: str, language: str = "en") -> str:
        """Transcribe an audio file to text."""
        segments, info = self._model.transcribe(file_path, language=language, beam_size=5)
        text = " ".join(segment.text.strip() for segment in segments)
        logger.debug(
            "Transcribed {:.1f}s audio → {} chars (lang={}, prob={:.2f})",
            info.duration,
            len(text),
            info.language,
            info.language_probability,
        )
        return text
