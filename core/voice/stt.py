"""WhisperSTT — speech-to-text using faster-whisper (local, GPU-accelerated)."""

from __future__ import annotations

import string
import tempfile
from typing import TYPE_CHECKING

from loguru import logger

from shared.traced import traced

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

# Whisper emits these verbatim from silence or room tone, and does so with
# no_speech_prob ~= 0.0 — the decoder is confident, so its own confidence
# signals cannot be used to filter them. Matched against the WHOLE transcript
# only; the same words inside a longer sentence are real speech.
_HALLUCINATIONS = frozenset(
    {
        "",
        "you",
        "thank you",
        "thanks",
        "thanks for watching",
        "thank you for watching",
        "we'll see you next time",
        "see you next time",
        "bye",
        "goodbye",
    }
)


def is_probable_hallucination(text: str) -> bool:
    """True when the whole transcript is a known Whisper non-speech artifact.

    Use on always-listening surfaces (voice satellites), where a false wake
    would otherwise get a spoken answer. Push-to-talk surfaces have explicit
    user intent and should not filter.
    """
    return text.strip().strip(string.punctuation + string.whitespace).lower() in _HALLUCINATIONS


class WhisperSTT:
    """Speech-to-text using faster-whisper (CTranslate2 backend).

    Runs entirely locally on GPU or CPU. No cloud dependency.
    """

    DEFAULT_MODEL = "large-v3-turbo"

    def __init__(
        self,
        model_size: str = DEFAULT_MODEL,
        device: str = "auto",
        compute_type: str = "auto",
    ) -> None:
        from faster_whisper import WhisperModel as _WhisperModel

        self._model: WhisperModel = _WhisperModel(
            model_size, device=device, compute_type=compute_type
        )
        logger.info("Loaded Whisper model: {} (device={})", model_size, device)

    @traced(name="voice.stt.transcribe")
    def transcribe(
        self, audio_bytes: bytes, language: str = "en", audio_format: str = "wav"
    ) -> str:
        """Transcribe audio bytes to text.

        Args:
            audio_bytes: Raw audio data (WAV, AAC, M4A, WebM, etc.)
            language: Language code for transcription.
            audio_format: File extension hint for ffmpeg (e.g., 'wav', 'aac', 'webm').

        Returns:
            Transcribed text string.
        """
        suffix = f".{audio_format.lstrip('.')}"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
            tmp.write(audio_bytes)
            tmp.flush()
            return self.transcribe_file(tmp.name, language=language)

    @traced(name="voice.stt.transcribe_file")
    def transcribe_file(self, file_path: str, language: str = "en") -> str:
        """Transcribe an audio file to text."""
        segments, info = self._model.transcribe(
            file_path,
            language=language,
            beam_size=5,
            # Whisper decodes silence and room tone into confident text (see
            # is_probable_hallucination). vad_filter drops non-speech audio
            # before the decoder ever sees it; condition_on_previous_text=False
            # stops one hallucinated segment from seeding the next.
            vad_filter=True,
            condition_on_previous_text=False,
        )
        text = " ".join(segment.text.strip() for segment in segments)
        logger.debug(
            "Transcribed {:.1f}s audio → {} chars (lang={}, prob={:.2f})",
            info.duration,
            len(text),
            info.language,
            info.language_probability,
        )
        return text
