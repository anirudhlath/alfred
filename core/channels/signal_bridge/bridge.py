"""Signal bridge — forwards Signal messages to/from Alfred Redis streams."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from bus.schemas.events import AlfredResponse, UserRequest
from shared.streams import NOTIFICATIONS_STREAM, USER_REQUESTS_STREAM, USER_RESPONSES_STREAM

if TYPE_CHECKING:
    from core.reflex.runner import AioRedis

logger = logging.getLogger(__name__)


class SignalBridge:
    """Bridges Signal CLI <-> Alfred Redis Streams."""

    _GROUP = "signal-bridge"
    _CONSUMER = "worker-1"

    def __init__(self, redis: AioRedis, phone_number: str) -> None:
        self._redis = redis
        self._phone = phone_number

    async def forward_inbound(self, sender: str, message: str, timestamp: str) -> None:
        """Forward an inbound Signal message to the user requests stream."""
        request = UserRequest(
            source="signal-bridge",
            channel="signal",
            session_id=f"signal-{sender}",
            identity_claim=sender,
            authenticated=False,
            content_type="text",
            content=message,
        )
        await self._redis.xadd(USER_REQUESTS_STREAM, {"event": request.model_dump_json()})
        logger.info("Forwarded Signal message from %s to Alfred", sender[:6])

    async def _send_signal(self, recipient: str, message: str) -> None:
        """Send a message via signal-cli. Placeholder for subprocess call."""
        # TODO: Implement actual signal-cli subprocess integration
        logger.info("Would send to %s: %s", recipient[:6], message[:50])

    async def send_notification(self, recipient: str, message: str) -> None:
        """Send an outbound notification via Signal."""
        await self._send_signal(recipient, message)

    async def ensure_consumer_group(self) -> None:
        """Create the consumer group if it doesn't exist."""
        import contextlib

        from redis.exceptions import ResponseError

        with contextlib.suppress(ResponseError):
            await self._redis.xgroup_create(
                NOTIFICATIONS_STREAM, self._GROUP, id="0", mkstream=True
            )

    async def poll_notifications(self) -> None:
        """Poll the notifications stream via consumer group and send via Signal."""
        entries: list[Any] = await self._redis.xreadgroup(
            self._GROUP, self._CONSUMER, {NOTIFICATIONS_STREAM: ">"}, count=10, block=5000
        )
        for _stream, stream_entries in entries:
            for entry_id, entry_data in stream_entries:
                raw = entry_data.get(b"event") or entry_data.get("event")
                if raw:
                    event_str = raw.decode() if isinstance(raw, bytes) else raw
                    event = json.loads(event_str)
                    await self.send_notification(self._phone, f"{event['title']}: {event['body']}")
                await self._redis.xack(  # type: ignore[no-untyped-call]
                    NOTIFICATIONS_STREAM, self._GROUP, entry_id
                )

    async def poll_responses(self, last_id: str = "$") -> str:
        """Poll USER_RESPONSES_STREAM for responses targeting the signal channel.

        Returns the last-seen stream ID for the next call.
        Uses plain xread (no consumer group) since responses may be read
        by multiple channel consumers.
        """
        entries: list[Any] = await self._redis.xread(
            {USER_RESPONSES_STREAM: last_id}, count=10, block=5000
        )
        for _stream, stream_entries in entries:
            for entry_id, entry_data in stream_entries:
                last_id = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
                raw = entry_data.get(b"event") or entry_data.get("event")
                if raw:
                    event_str = raw.decode() if isinstance(raw, bytes) else raw
                    resp = AlfredResponse.model_validate_json(event_str)
                    if resp.channel == "signal":
                        recipient = resp.session_id.removeprefix("signal-")
                        await self.send_notification(recipient, resp.text)
        return last_id
