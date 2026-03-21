"""Signal channel adapter — delivers notifications via Signal Bridge."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

from core.notifications.channels import ChannelAdapter, ChannelRegistry
from core.notifications.schema import Notification, Urgency

if TYPE_CHECKING:
    from core.channels.signal_bridge.bridge import SignalBridge

logger = logging.getLogger(__name__)


@ChannelRegistry.register()
class SignalChannelAdapter(ChannelAdapter):
    """Deliver notifications via Signal messenger."""

    name: ClassVar[str] = "signal"
    supported_urgencies: ClassVar[set[Urgency]] = {
        Urgency.INFORMATIONAL,
        Urgency.IMPORTANT,
        Urgency.URGENT,
    }

    def __init__(
        self,
        bridge: SignalBridge | None = None,
        recipient: str = "",
    ) -> None:
        self._bridge = bridge
        self._recipient = recipient

    async def deliver(self, notification: Notification) -> None:
        """Format and send notification via Signal Bridge."""
        if self._bridge is None:
            logger.warning("SignalChannelAdapter: no bridge configured, skipping")
            return
        message = f"{notification.title}: {notification.body}"
        await self._bridge.send_notification(self._recipient, message)
