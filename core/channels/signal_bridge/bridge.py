"""Signal bridge — forwards Signal messages to/from Alfred Redis streams."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from bus.schemas.events import AlfredResponse, UserRequest
from shared.streams import (
    NOTIFICATIONS_STREAM,
    USER_REQUESTS_STREAM,
    USER_RESPONSES_STREAM,
    decode_stream_value,
)

if TYPE_CHECKING:
    from shared.types import AioRedis

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
        """Send a message via signal-cli subprocess."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "signal-cli",
                "-u",
                self._phone,
                "send",
                "-m",
                message,
                recipient,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning(
                    "signal-cli send failed (code %d): %s",
                    proc.returncode,
                    stderr.decode(errors="replace")[:200],
                )
            else:
                logger.info("Sent Signal message to %s", recipient[:6])
        except FileNotFoundError:
            logger.error("signal-cli not found — install it to enable Signal delivery")
        except Exception as exc:
            logger.warning("Failed to send Signal message: %s", exc)

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
                    event_str = decode_stream_value(raw)
                    event = json.loads(event_str)
                    await self.send_notification(self._phone, f"{event['title']}: {event['body']}")
                await self._redis.xack(NOTIFICATIONS_STREAM, self._GROUP, entry_id)

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
                last_id = decode_stream_value(entry_id)
                raw = entry_data.get(b"event") or entry_data.get("event")
                if raw:
                    event_str = decode_stream_value(raw)
                    resp = AlfredResponse.model_validate_json(event_str)
                    if resp.channel == "signal":
                        recipient = resp.session_id.removeprefix("signal-")
                        await self.send_notification(recipient, resp.text)
        return last_id
