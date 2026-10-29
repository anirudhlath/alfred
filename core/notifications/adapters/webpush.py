"""Web Push channel adapter — pushes notifications to browsers (RFC 8030, 8291, 8292).

Every Web Push subscription lives in ``DEVICE_TOKENS_KEY`` beside the APNs tokens (see
``core/notifications/devices.py``). Each push is the notification as compact JSON,
encrypted to the subscription's keys (``aes128gcm``) and POSTed to its endpoint with a
VAPID ``Authorization`` header. The push service answers 404 or 410 for a subscription
the browser has dropped, and only then is the row pruned.

The endpoint, ``p256dh`` and ``auth`` are credentials, so nothing here logs them: a line
names the field's first 12 characters and the push host instead.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import TYPE_CHECKING, Any, ClassVar, Final

import http_ece
from cryptography.hazmat.primitives.asymmetric import ec
from loguru import logger

from core.notifications.channels import ChannelAdapter, ChannelRegistry
from core.notifications.devices import decode_b64url, decode_device, is_web_push, push_host
from core.notifications.schema import Notification, Urgency
from shared.streams import DEVICE_TOKENS_KEY

if TYPE_CHECKING:
    import httpx

    from core.notifications.vapid import VapidKeys
    from shared.types import AioRedis

URGENCY_HEADER: Final = {Urgency.IMPORTANT: "normal", Urgency.URGENT: "high"}
TTL_SECONDS: Final = {Urgency.URGENT: 3600, Urgency.IMPORTANT: 86400}
PRUNE_STATUSES: Final = frozenset({404, 410})
# Push services take 4096 bytes of encrypted body; aes128gcm adds 103 (an 86-byte header
# carrying the sender key, the 16-byte tag, the padding delimiter). 3072 leaves room.
MAX_PAYLOAD_BYTES: Final = 3072

_ELLIPSIS: Final = "…"
_SALT_LEN: Final = 16
_FIELD_PREFIX: Final = 12  # how much of a device field a log line may show
_MAX_RESPONSE_TEXT: Final = 200


def _encode(fields: dict[str, str | None]) -> bytes:
    return json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode()


def build_payload(notification: Notification) -> bytes:
    """The push's plaintext: compact JSON the service worker reads, at most MAX_PAYLOAD_BYTES.

    A body too long to fit is cut to the longest prefix that does and ends in an ellipsis.
    """
    action_id = notification.metadata.get("pending_action_id")
    fields: dict[str, str | None] = {
        "title": notification.title,
        "body": notification.body,
        "urgency": notification.urgency.value,
        "id": notification.notification_id,
        "action_id": action_id if isinstance(action_id, str) else None,
    }
    encoded = _encode(fields)
    if len(encoded) <= MAX_PAYLOAD_BYTES:
        return encoded

    def shortened(keep: int) -> bytes:
        return _encode({**fields, "body": notification.body[:keep] + _ELLIPSIS})

    # Characters encode to one byte or several (UTF-8, JSON escapes), so search for the
    # longest prefix that fits rather than counting bytes. The whole body does not fit.
    fits, too_long = 0, len(notification.body)
    while too_long - fits > 1:
        middle = (fits + too_long) // 2
        if len(shortened(middle)) <= MAX_PAYLOAD_BYTES:
            fits = middle
        else:
            too_long = middle
    return shortened(fits)


def encrypt(plaintext: bytes, p256dh: str, auth: str) -> bytes:
    """``plaintext`` encrypted to a subscription's keys (RFC 8291, ``aes128gcm``).

    Each call uses a fresh ephemeral key and salt; http_ece puts the ephemeral public key
    in the body's header, where the browser reads it.
    """
    _, receiver = decode_b64url(p256dh, "p256dh")
    _, auth_secret = decode_b64url(auth, "auth")
    body: bytes = http_ece.encrypt(
        plaintext,
        salt=os.urandom(_SALT_LEN),
        private_key=ec.generate_private_key(ec.SECP256R1()),
        dh=receiver,
        auth_secret=auth_secret,
        version="aes128gcm",
    )
    return body


@ChannelRegistry.register()
class WebPushChannelAdapter(ChannelAdapter):
    """Push notifications to subscribed browsers via their push services."""

    name: ClassVar[str] = "webpush"
    supported_urgencies: ClassVar[set[Urgency]] = {Urgency.IMPORTANT, Urgency.URGENT}

    def __init__(
        self,
        redis: AioRedis,
        keys: VapidKeys,
        subject: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._redis = redis
        self._keys = keys
        self._subject = subject
        self._client = client
        self._owns_client = False

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Lazy-init httpx client with HTTP/2 support."""
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(http2=True, timeout=10.0)
            self._owns_client = True
        return self._client

    async def close(self) -> None:
        """Close the HTTP client, if this adapter built it; a client handed in is the caller's."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
            self._owns_client = False

    async def deliver(self, notification: Notification) -> None:
        """Deliver notification to every Web Push subscription."""
        rows: dict[bytes | str, bytes | str] = await self._redis.hgetall(DEVICE_TOKENS_KEY)
        subscriptions: list[tuple[str, dict[str, Any]]] = []
        for raw_field, value in rows.items():
            row = decode_device(value)
            if row is not None and is_web_push(row):  # APNs tokens are the APNs adapter's
                field = raw_field.decode() if isinstance(raw_field, bytes) else raw_field
                subscriptions.append((field, row))
        if not subscriptions:
            logger.debug("WebPushChannelAdapter: no web push subscriptions, skipping")
            return

        client = await self._ensure_client()
        payload = build_payload(notification)
        urgency = notification.urgency
        headers: dict[str, str] = {
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(TTL_SECONDS[urgency]),
            "Urgency": URGENCY_HEADER[urgency],
        }

        async def _send(field: str, row: dict[str, Any]) -> None:
            prefix = field[:_FIELD_PREFIX]
            host = ""
            try:
                endpoint = row["endpoint"]
                host = push_host(endpoint)
                keys = row["keys"]
                body = encrypt(payload, keys["p256dh"], keys["auth"])
                # aud is the push service's origin, so each endpoint signs its own token.
                authorization = self._keys.authorization(endpoint, self._subject, time.time())
                resp = await client.post(
                    endpoint, content=body, headers={**headers, "Authorization": authorization}
                )
                if resp.is_success:
                    logger.info(
                        "Web push notification sent ({}…, {}, urgency={})",
                        prefix,
                        host,
                        urgency.value,
                    )
                elif resp.status_code in PRUNE_STATUSES:
                    await self._redis.hdel(DEVICE_TOKENS_KEY, field)
                    logger.info("Pruned stale web push subscription {}… ({})", prefix, host)
                else:
                    logger.warning(
                        "Web push delivery failed for {}… ({}): {} {}",
                        prefix,
                        host,
                        resp.status_code,
                        resp.text[:_MAX_RESPONSE_TEXT],
                    )
            except Exception as exc:
                # The exception's type only: httpx's messages can quote the request URL,
                # which is the endpoint.
                logger.error(
                    "Web push delivery error for {}… ({}): {}", prefix, host, type(exc).__name__
                )

        await asyncio.gather(*(_send(field, row) for field, row in subscriptions))
