"""WebSocket channel adapter — pushes notifications to connected web sessions."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any, ClassVar

from loguru import logger

from core.channels.voice_models import synthesize_async
from core.notifications.channels import ChannelAdapter, ChannelRegistry
from core.notifications.schema import Notification, Urgency

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from core.voice.tts_backend import TTSBackend


@ChannelRegistry.register()
class WebSocketChannelAdapter(ChannelAdapter):
    """Push notification JSON to all connected WebSocket sessions."""

    name: ClassVar[str] = "websocket"
    supported_urgencies: ClassVar[set[Urgency]] = {Urgency.IMPORTANT, Urgency.URGENT}

    def __init__(
        self,
        get_sessions: Callable[[], list[Any]] | None = None,
        aget_tts: Callable[[], Awaitable[TTSBackend | None]] | None = None,
    ) -> None:
        self._get_sessions = get_sessions
        # Async getter — TTS construction is a cold 10-40s load (a 353 MB model
        # on first use) and must never run synchronously on the event loop
        # that's also serving WebSockets/notifications (mirrors
        # SatelliteChannelAdapter — see core/notifications/adapters/satellite.py).
        self._aget_tts = aget_tts

    async def deliver(self, notification: Notification) -> None:
        """Push notification to all active WebSocket connections."""
        if self._get_sessions is None:
            logger.debug("WebSocketChannelAdapter: no session getter, skipping")
            return
        sessions = self._get_sessions()
        if not sessions:
            logger.debug("WebSocketChannelAdapter: no active sessions, skipping")
            return

        payload: dict[str, Any] = {
            "type": "notification",
            "title": notification.title,
            "body": notification.body,
            "urgency": notification.urgency.value,
            "notification_id": notification.notification_id,
            "metadata": notification.metadata,
        }

        if notification.urgency == Urgency.URGENT and self._aget_tts is not None:
            tts = await self._aget_tts()
            if tts is not None:
                try:
                    text = f"{notification.title}: {notification.body}"
                    wav_bytes = await synthesize_async(tts, text)
                    payload["audio"] = base64.b64encode(wav_bytes).decode()
                except Exception as exc:
                    logger.warning("WebSocketChannelAdapter: TTS synthesis failed: {}", exc)

        for ws in sessions:
            try:
                await ws.send_json(payload)
            except Exception as exc:
                logger.warning("Failed to push notification to WebSocket: {}", exc)
