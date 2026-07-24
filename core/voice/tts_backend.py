"""TTSBackend — the abstract port every TTS adapter implements.

The channels process depends only on this abstraction, never on a concrete
engine. New backends (Kokoro, Piper, a future Apple-Silicon MLX adapter) subclass
this and register in ``core.voice.tts_registry`` with no caller changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class TTSBackend(ABC):
    """Adapter port for local text-to-speech."""

    @abstractmethod
    def synthesize(self, text: str) -> bytes:
        """Synthesize ``text`` to 16-bit PCM mono WAV bytes."""
        raise NotImplementedError
