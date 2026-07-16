"""SatellitePipeline — utterance orchestration with all I/O faked."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from bus.schemas.events import AlfredResponse
from core.channels.satellite.config import SatelliteEntry
from core.channels.satellite.pipeline import SatellitePipeline
from core.voice.speaker_id import SpeakerMatch

PCM = b"\x01\x00" * 16000


def _conn(name: str = "kitchen", area: str | None = "Kitchen") -> AsyncMock:
    conn = AsyncMock()
    conn.entry = SatelliteEntry(name=name, host="h", area=area)
    return conn


def _stt(text: str = "turn off the lights") -> MagicMock:
    stt = MagicMock()
    stt.transcribe = MagicMock(return_value=text)
    return stt


def _tts() -> MagicMock:
    tts = MagicMock()
    tts.synthesize = MagicMock(return_value=b"RIFFfakewav")
    return tts


def _pipeline(
    stt: Any, tts: Any, speaker_id: Any = None, response_text: str = "Done, sir."
) -> tuple[SatellitePipeline, AsyncMock]:
    publish = AsyncMock(
        return_value=AlfredResponse(
            source="conscious-engine",
            channel="satellite",
            session_id="sat-kitchen",
            text=response_text,
        )
    )

    async def get_stt() -> Any:
        return stt

    async def get_tts() -> Any:
        return tts

    pipeline = SatellitePipeline(
        AsyncMock(), get_stt=get_stt, get_tts=get_tts, speaker_id=speaker_id
    )
    return pipeline, publish


async def test_happy_path_sends_transcript_then_audio() -> None:
    conn = _conn()
    pipeline, publish = _pipeline(_stt(), _tts())
    with patch("core.channels.satellite.pipeline.publish_and_wait", publish):
        await pipeline(conn, PCM)

    conn.send_transcript.assert_awaited_once_with("turn off the lights")
    conn.send_synthesize.assert_awaited_once_with("Done, sir.")
    conn.play_wav.assert_awaited_once_with(b"RIFFfakewav")

    request = publish.call_args.args[1]
    assert request.channel == "satellite"
    assert request.device_id == "kitchen"
    assert request.area == "Kitchen"
    assert request.session_id == "sat-kitchen"
    assert request.content_type == "audio"
    assert request.identity_claim == "sir"
    assert request.identity_confidence is None


async def test_speaker_match_sets_identity_confidence() -> None:
    speaker_id = AsyncMock()
    speaker_id.identify = AsyncMock(
        return_value=SpeakerMatch(identity="sir", confidence=0.88, enrolled=True)
    )
    pipeline, publish = _pipeline(_stt(), _tts(), speaker_id=speaker_id)
    with patch("core.channels.satellite.pipeline.publish_and_wait", publish):
        await pipeline(_conn(), PCM)
    request = publish.call_args.args[1]
    assert request.identity_confidence == 0.88


async def test_empty_transcript_stops_early() -> None:
    conn = _conn()
    pipeline, publish = _pipeline(_stt(text="  "), _tts())
    with patch("core.channels.satellite.pipeline.publish_and_wait", publish):
        await pipeline(conn, PCM)
    conn.send_transcript.assert_awaited_once_with("")
    publish.assert_not_awaited()
    conn.play_wav.assert_not_awaited()


async def test_stt_unavailable_sends_error() -> None:
    conn = _conn()
    pipeline, publish = _pipeline(None, _tts())
    with patch("core.channels.satellite.pipeline.publish_and_wait", publish):
        await pipeline(conn, PCM)
    conn.send_error.assert_awaited_once()
    conn.send_transcript.assert_awaited_once_with("")
    publish.assert_not_awaited()


async def test_speaker_id_failure_is_nonfatal() -> None:
    speaker_id = AsyncMock()
    speaker_id.identify = AsyncMock(side_effect=RuntimeError("model exploded"))
    conn = _conn()
    pipeline, publish = _pipeline(_stt(), _tts(), speaker_id=speaker_id)
    with patch("core.channels.satellite.pipeline.publish_and_wait", publish):
        await pipeline(conn, PCM)
    request = publish.call_args.args[1]
    assert request.identity_claim == "sir"
    assert request.identity_confidence is None
