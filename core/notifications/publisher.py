"""NotificationPublisher — creates Notification objects and routes through Dispatcher."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.notifications.schema import Notification, Urgency

if TYPE_CHECKING:
    from core.notifications.dispatcher import NotificationDispatcher


class NotificationPublisher:
    """Creates and publishes notifications through the Dispatcher.

    This is the public API that other components (CostTracker, Conscious Engine,
    etc.) call to send notifications. The Dispatcher handles DND + channel routing.
    """

    def __init__(self, dispatcher: NotificationDispatcher) -> None:
        self._dispatcher = dispatcher

    async def publish(
        self,
        title: str,
        body: str,
        source: str,
        urgency: Urgency = Urgency.INFORMATIONAL,
    ) -> None:
        """Create a Notification and route it through the dispatcher."""
        notification = Notification(
            title=title,
            body=body,
            urgency=urgency,
            source=source,
        )
        await self._dispatcher.dispatch(notification)
