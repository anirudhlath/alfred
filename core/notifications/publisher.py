"""NotificationPublisher — creates Notification objects and routes through Dispatcher."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any

from core.notifications.schema import Notification, Urgency

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from core.notifications.dispatcher import NotificationDispatcher


class NotificationPublisher:
    """Creates and publishes notifications through the Dispatcher.

    This is the public API that other components (the spend alert, Conscious Engine,
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
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Create a Notification and route it through the dispatcher."""
        notification = Notification(
            title=title,
            body=body,
            urgency=urgency,
            source=source,
            metadata=metadata or {},
        )
        await self._dispatcher.dispatch(notification)

    def urgent(self, source: str) -> Callable[[str, str], Awaitable[None]]:
        """A ``send(title, body)`` for *source*'s alerts, at a DND-bypassing urgency."""
        return partial(self.publish, source=source, urgency=Urgency.URGENT)
