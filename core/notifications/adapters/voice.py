"""Voice channel adapter — TTS synthesis + WebSocket audio push."""

from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING, Any, ClassVar

from core.notifications.channels import ChannelAdapter, ChannelRegistry
from core.notifications.schema import Notification, Urgency

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


@ChannelRegistry.register()
class VoiceChannelAdapter(ChannelAdapter):
    """Synthesize notification text to audio and push via WebSocket."""

    name: ClassVar[str] = "voice"
    supported_urgencies: ClassVar[set[Urgency]] = {Urgency.URGENT}

    def __init__(
        self,
        get_tts: Callable[[], Any | None] | None = None,
        get_sessions: Callable[[], list[Any]] | None = None,
    ) -> None:
        self._get_tts = get_tts
        self._get_sessions = get_sessions

    async def deliver(self, notification: Notification) -> None:
        """Synthesize text to audio and push to WebSocket sessions."""
        if self._get_sessions is None:
            return
        sessions = self._get_sessions()
        if not sessions:
            logger.debug("VoiceChannelAdapter: no active sessions, skipping")
            return

        if self._get_tts is None:
            return
        tts = self._get_tts()
        if tts is None:
            logger.debug("VoiceChannelAdapter: TTS not available, skipping")
            return

        text = f"{notification.title}: {notification.body}"
        try:
            wav_bytes: bytes = tts.synthesize(text)
        except Exception as exc:
            logger.error("VoiceChannelAdapter: TTS synthesis failed: %s", exc)
            return

        audio_b64 = base64.b64encode(wav_bytes).decode()
        payload = {
            "type": "voice_notification",
            "title": notification.title,
            "audio": audio_b64,
        }
        for ws in sessions:
            try:
                await ws.send_json(payload)
            except Exception as exc:
                logger.warning("Failed to push voice notification to WebSocket: %s", exc)
