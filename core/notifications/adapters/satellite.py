"""Satellite channel adapter — speaks URGENT notifications on all online satellites."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from loguru import logger

from core.channels.voice_models import synthesize_async
from core.notifications.channels import ChannelAdapter, ChannelRegistry
from core.notifications.schema import Notification, Urgency

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@ChannelRegistry.register()
class SatelliteChannelAdapter(ChannelAdapter):
    """Piper-synthesized speech pushed over the satellite bridge connections."""

    name: ClassVar[str] = "satellite"
    supported_urgencies: ClassVar[set[Urgency]] = {Urgency.URGENT}

    def __init__(
        self,
        get_bridge: Callable[[], Any | None],
        get_tts: Callable[[], Awaitable[Any | None]],
    ) -> None:
        self._get_bridge = get_bridge
        # Async getter — TTS construction is a cold 10-40s load (possibly a
        # blocking HF download) and must never run synchronously on the event
        # loop that's also serving WebSockets/notifications.
        self._get_tts = get_tts

    async def deliver(self, notification: Notification) -> None:
        """Speak the notification on every online satellite."""
        bridge = self._get_bridge()
        tts = await self._get_tts()
        if bridge is None or tts is None:
            logger.debug("SatelliteChannelAdapter: bridge/TTS unavailable, skipping")
            return
        text = f"{notification.title}: {notification.body}"
        try:
            wav = await synthesize_async(tts, text)
        except Exception as exc:
            logger.warning("SatelliteChannelAdapter: TTS failed: {}", exc)
            return
        delivered = await bridge.play_wav_all(wav)
        logger.info("Announcement delivered to {} satellite(s)", delivered)
