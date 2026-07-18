"""UtteranceCollector — streaming VAD endpointing with scripted probabilities."""

from collections import deque

from core.channels.satellite.endpointing import CollectorEvent, UtteranceCollector

FRAME = b"\x01\x00" * 512  # one 32ms frame (1024 bytes)
_MS_PER_FRAME = 32


def _collector(probs: list[float], **kwargs: object) -> UtteranceCollector:
    q = deque(probs)
    return UtteranceCollector(vad=lambda _frame: q.popleft() if q else 0.0, **kwargs)  # type: ignore[arg-type]


def _feed_frames(c: UtteranceCollector, n: int) -> list[CollectorEvent]:
    events: list[CollectorEvent] = []
    for _ in range(n):
        events.extend(c.feed(FRAME))
    return events


def test_speech_then_silence_emits_utterance() -> None:
    speech_frames = 10
    silence_frames = 800 // _MS_PER_FRAME + 1  # cross the 800ms end threshold
    c = _collector([0.9] * speech_frames + [0.0] * (silence_frames + 5))
    events = _feed_frames(c, speech_frames + silence_frames + 5)
    kinds = [e.kind for e in events]
    assert kinds[0] == "speech_start"
    assert "utterance" in kinds
    utterance = next(e for e in events if e.kind == "utterance")
    assert utterance.pcm is not None
    # utterance includes the spoken frames (plus pre-roll/tail padding)
    assert len(utterance.pcm) >= speech_frames * len(FRAME)


def test_no_speech_times_out() -> None:
    frames = 8000 // _MS_PER_FRAME + 2
    c = _collector([0.0] * frames)
    events = _feed_frames(c, frames)
    assert [e.kind for e in events] == ["timeout"]


def test_max_utterance_forces_end() -> None:
    frames = 15000 // _MS_PER_FRAME + 5
    c = _collector([0.9] * frames)
    events = _feed_frames(c, frames)
    assert events[0].kind == "speech_start"
    assert events[-1].kind == "utterance"


def test_exhausted_after_utterance() -> None:
    c = _collector([0.9] * 10 + [0.0] * 40)
    _feed_frames(c, 50)
    assert c.feed(FRAME) == []


def test_partial_chunks_are_buffered() -> None:
    """Feeding half-frames must not crash and must eventually frame up."""
    c = _collector([0.0] * 10)
    assert c.feed(FRAME[:512]) == []
    assert c.feed(FRAME[512:]) == []  # completes exactly one frame → one vad call
