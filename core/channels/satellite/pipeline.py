"""Satellite utterance pipeline — STT → Conscious Engine → TTS reply."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from loguru import logger

from bus.schemas.events import UserRequest
from core.channels.request_bus import publish_and_wait
from core.channels.satellite.audio import pcm_to_wav

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from core.channels.satellite.bridge import SatelliteConnection


class SatellitePipeline:
    """UtteranceHandler implementation: full voice loop for one utterance."""

    def __init__(
        self,
        redis: Any,
        *,
        get_stt: Callable[[], Awaitable[Any]],
        get_tts: Callable[[], Awaitable[Any]],
        speaker_id: Any = None,
        request_timeout: float = 60.0,
    ) -> None:
        self._redis = redis
        self._get_stt = get_stt
        self._get_tts = get_tts
        self._speaker_id = speaker_id
        self._request_timeout = request_timeout

    async def __call__(self, conn: SatelliteConnection, pcm: bytes) -> None:
        entry = conn.entry

        stt = await self._get_stt()
        if stt is None:
            logger.error("Satellite '{}': STT unavailable", entry.name)
            await conn.send_error("Voice processing unavailable")
            await conn.send_transcript("")  # re-arm the satellite
            return

        wav = pcm_to_wav(pcm)
        text = (await asyncio.to_thread(stt.transcribe, wav, audio_format="wav")).strip()
        # Transcript FIRST: it stops mic streaming and re-arms the satellite
        # before the slow LLM round-trip.
        await conn.send_transcript(text)
        if not text:
            logger.debug("Satellite '{}': empty transcript", entry.name)
            return
        logger.info("Satellite '{}' heard: {}", entry.name, text)

        identity_claim: str = "sir"
        identity_confidence: float | None = None
        if self._speaker_id is not None:
            try:
                match = await self._speaker_id.identify(pcm)
                if match.enrolled:
                    identity_claim = match.identity
                    identity_confidence = match.confidence
            except Exception as exc:
                logger.warning("Satellite '{}': speaker ID failed: {}", entry.name, exc)

        session_id = f"sat-{entry.name}"
        request = UserRequest(
            source="satellite",
            channel="satellite",
            session_id=session_id,
            identity_claim=identity_claim,
            identity_confidence=identity_confidence,
            authenticated=False,
            content_type="audio",
            content=text,
            device_id=entry.name,
            area=entry.area,
        )
        response = await publish_and_wait(
            self._redis, request, session_id, timeout=self._request_timeout
        )

        await conn.send_synthesize(response.text)
        tts = await self._get_tts()
        if tts is None:
            logger.warning("Satellite '{}': TTS unavailable — reply not spoken", entry.name)
            return
        wav_out = await asyncio.to_thread(tts.synthesize, response.text)
        await conn.play_wav(wav_out)
