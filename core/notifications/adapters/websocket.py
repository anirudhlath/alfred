"""WebSocket channel adapter — pushes notifications to connected web sessions."""

from __future__ import annotations

import asyncio
import base64
from typing import TYPE_CHECKING, Any, ClassVar

from loguru import logger

from core.notifications.channels import ChannelAdapter, ChannelRegistry
from core.notifications.schema import Notification, Urgency

if TYPE_CHECKING:
    from collections.abc import Callable


@ChannelRegistry.register()
class WebSocketChannelAdapter(ChannelAdapter):
    """Push notification JSON to all connected WebSocket sessions."""

    name: ClassVar[str] = "websocket"
    supported_urgencies: ClassVar[set[Urgency]] = {Urgency.IMPORTANT, Urgency.URGENT}

    def __init__(
        self,
        get_sessions: Callable[[], list[Any]] | None = None,
        get_tts: Callable[[], Any | None] | None = None,
    ) -> None:
        self._get_sessions = get_sessions
        self._get_tts = get_tts

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
        }

        if notification.urgency == Urgency.URGENT and self._get_tts is not None:
            tts = self._get_tts()
            if tts is not None:
                try:
                    text = f"{notification.title}: {notification.body}"
                    wav_bytes: bytes = await asyncio.to_thread(tts.synthesize, text)
                    payload["audio"] = base64.b64encode(wav_bytes).decode()
                except Exception as exc:
                    logger.warning("WebSocketChannelAdapter: TTS synthesis failed: {}", exc)

        for ws in sessions:
            try:
                await ws.send_json(payload)
            except Exception as exc:
                logger.warning("Failed to push notification to WebSocket: {}", exc)
