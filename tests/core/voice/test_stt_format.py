from unittest.mock import MagicMock, patch

from core.voice.stt import WhisperSTT


def _make_stt() -> WhisperSTT:
    """Create a WhisperSTT instance with mocked model (skip __init__ model load)."""
    stt = object.__new__(WhisperSTT)
    stt._model = MagicMock()
    return stt


def test_transcribe_uses_correct_suffix_for_aac() -> None:
    """Verify the tempfile suffix matches the audio format."""
    stt = _make_stt()
    with patch.object(stt, "transcribe_file", return_value="hello world"):
        stt.transcribe(b"fake-aac-bytes", language="en", audio_format="aac")
        call_args = stt.transcribe_file.call_args
        # The temp file path should end with .aac
        assert call_args[0][0].endswith(".aac")


def test_transcribe_defaults_to_wav_suffix() -> None:
    """Without audio_format, suffix should be .wav for backward compat."""
    stt = _make_stt()
    with patch.object(stt, "transcribe_file", return_value="hello world"):
        stt.transcribe(b"fake-wav-bytes", language="en")
        call_args = stt.transcribe_file.call_args
        assert call_args[0][0].endswith(".wav")
