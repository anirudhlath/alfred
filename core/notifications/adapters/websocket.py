"""WebSocket channel adapter — pushes notifications to connected web sessions."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar

from core.notifications.channels import ChannelAdapter, ChannelRegistry
from core.notifications.schema import Notification, Urgency

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


@ChannelRegistry.register()
class WebSocketChannelAdapter(ChannelAdapter):
    """Push notification JSON to all connected WebSocket sessions."""

    name: ClassVar[str] = "websocket"
    supported_urgencies: ClassVar[set[Urgency]] = {Urgency.IMPORTANT, Urgency.URGENT}

    def __init__(self, get_sessions: Callable[[], list[Any]] | None = None) -> None:
        self._get_sessions = get_sessions

    async def deliver(self, notification: Notification) -> None:
        """Push notification to all active WebSocket connections."""
        if self._get_sessions is None:
            logger.debug("WebSocketChannelAdapter: no session getter, skipping")
            return
        sessions = self._get_sessions()
        if not sessions:
            logger.debug("WebSocketChannelAdapter: no active sessions, skipping")
            return

        payload = {
            "type": "notification",
            "title": notification.title,
            "body": notification.body,
            "urgency": notification.urgency.value,
        }
        for ws in sessions:
            try:
                await ws.send_json(payload)
            except Exception as exc:
                logger.warning("Failed to push notification to WebSocket: %s", exc)
