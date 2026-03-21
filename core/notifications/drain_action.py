"""Action handler for draining deferred notifications.

Called by the Trigger Engine when a DND-expiry time trigger fires.
The trigger's ActionRequest targets this handler.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.notifications.dispatcher import NotificationDispatcher

logger = logging.getLogger(__name__)


async def handle_drain_deferred(dispatcher: NotificationDispatcher) -> None:
    """Drain all deferred notifications. Called when DND expiry trigger fires."""
    logger.info("DND expiry trigger fired — draining deferred notifications")
    await dispatcher.drain_deferred()
