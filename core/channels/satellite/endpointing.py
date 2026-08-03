"""Streaming utterance endpointing — silero VAD probabilities + hysteresis.

The satellite streams mic audio indefinitely after wake; the server decides
when the command has ended. Start when prob >= threshold; end when prob stays
below end_threshold for silence_ms. An utterance must also contain at least
min_speech_ms of voiced frames, otherwise it is reported as no-speech — a
false wake on TV audio trips the VAD only briefly. Frames are 512 samples
(1024 bytes) of 16 kHz s16 mono PCM — pysilero-vad's fixed chunk size.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable

FRAME_BYTES = 1024  # 512 samples @ 16kHz s16 mono = 32 ms
_MS_PER_FRAME = 32
_PRE_ROLL_FRAMES = 10  # 320 ms kept before speech start


@dataclass(frozen=True)
class CollectorEvent:
    """State transition emitted by the collector."""

    kind: Literal["speech_start", "utterance", "timeout"]
    pcm: bytes | None = None


class UtteranceCollector:
    """Single-utterance collector. Create one per pipeline run; not reusable."""

    def __init__(
        self,
        vad: Callable[[bytes], float],
        *,
        threshold: float = 0.5,
        end_threshold: float = 0.35,
        silence_ms: int = 800,
        no_speech_timeout_ms: int = 8000,
        max_utterance_ms: int = 15000,
        min_speech_ms: int = 250,
    ) -> None:
        self._vad = vad
        self._threshold = threshold
        self._end_threshold = end_threshold
        self._silence_frames_needed = max(1, silence_ms // _MS_PER_FRAME)
        self._no_speech_frames = max(1, no_speech_timeout_ms // _MS_PER_FRAME)
        self._max_frames = max(1, max_utterance_ms // _MS_PER_FRAME)
        self._min_voiced_frames = max(1, min_speech_ms // _MS_PER_FRAME)
        self._buffer = bytearray()
        self._pre_roll: deque[bytes] = deque(maxlen=_PRE_ROLL_FRAMES)
        self._speech: bytearray = bytearray()
        self._in_speech = False
        self._silence_run = 0
        self._frames_seen = 0
        self._speech_frames = 0
        self._voiced_frames = 0
        self._done = False

    def feed(self, pcm: bytes) -> list[CollectorEvent]:
        """Feed arbitrary-size PCM; returns zero or more state transitions."""
        if self._done:
            return []
        self._buffer.extend(pcm)
        events: list[CollectorEvent] = []
        while len(self._buffer) >= FRAME_BYTES and not self._done:
            frame = bytes(self._buffer[:FRAME_BYTES])
            del self._buffer[:FRAME_BYTES]
            events.extend(self._process_frame(frame))
        return events

    def _process_frame(self, frame: bytes) -> list[CollectorEvent]:
        self._frames_seen += 1
        prob = self._vad(frame)

        if not self._in_speech:
            self._pre_roll.append(frame)  # deque(maxlen=...) auto-evicts the oldest
            if prob >= self._threshold:
                self._in_speech = True
                self._voiced_frames = 1
                self._speech.extend(b"".join(self._pre_roll))
                return [CollectorEvent(kind="speech_start")]
            if self._frames_seen >= self._no_speech_frames:
                self._done = True
                return [CollectorEvent(kind="timeout")]
            return []

        self._speech.extend(frame)
        self._speech_frames += 1
        if prob < self._end_threshold:
            self._silence_run += 1
        else:
            self._silence_run = 0
            self._voiced_frames += 1

        if (
            self._silence_run >= self._silence_frames_needed
            or self._speech_frames >= self._max_frames
        ):
            self._done = True
            # A wake word that fires on TV audio typically trips the VAD for a
            # frame or two. Emitting that as an utterance hands Whisper ~1s of
            # near-silence, which it reliably decodes into text — so treat too
            # little actual speech as no speech at all.
            if self._voiced_frames < self._min_voiced_frames:
                return [CollectorEvent(kind="timeout")]
            return [CollectorEvent(kind="utterance", pcm=bytes(self._speech))]
        return []


def default_collector_factory() -> UtteranceCollector:
    """Real silero-backed collector (one detector per utterance; ggml, loads in ms)."""
    from pysilero_vad import SileroVoiceActivityDetector

    detector = SileroVoiceActivityDetector()
    return UtteranceCollector(vad=detector)
