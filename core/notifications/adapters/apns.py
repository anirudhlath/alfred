"""APNs channel adapter — pushes notifications to iOS devices via APNs."""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any, ClassVar

import jwt  # PyJWT[crypto]
from loguru import logger

from core.notifications.channels import ChannelAdapter, ChannelRegistry
from core.notifications.schema import Notification, Urgency
from shared.streams import DEVICE_TOKENS_KEY

if TYPE_CHECKING:
    import httpx
    import redis.asyncio as aioredis

APNS_PRODUCTION_URL = "https://api.push.apple.com"
APNS_SANDBOX_URL = "https://api.sandbox.push.apple.com"


@ChannelRegistry.register()
class APNsChannelAdapter(ChannelAdapter):
    """Push notifications to iOS devices via APNs HTTP/2 API."""

    name: ClassVar[str] = "apns"
    supported_urgencies: ClassVar[set[Urgency]] = {
        Urgency.INFORMATIONAL,
        Urgency.IMPORTANT,
        Urgency.URGENT,
    }

    def __init__(
        self,
        redis: aioredis.Redis[Any],  # type: ignore[type-arg]
        team_id: str,
        key_id: str,
        private_key: str,
        bundle_id: str,
        sandbox: bool | None = None,
    ) -> None:
        import os

        self._redis = redis
        self._team_id = team_id
        self._key_id = key_id
        # Normalize escaped newlines (keyring may store \\n instead of \n)
        self._private_key = private_key.replace("\\n", "\n")
        self._bundle_id = bundle_id
        if sandbox is None:
            sandbox = os.getenv("APNS_SANDBOX", "").lower() in ("1", "true", "yes")
        self._base_url = APNS_SANDBOX_URL if sandbox else APNS_PRODUCTION_URL
        self._client: httpx.AsyncClient | None = None
        self._token: str | None = None
        self._token_expires: float = 0

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Lazy-init httpx client with HTTP/2 support."""
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(http2=True, timeout=10.0)
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP/2 client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _get_auth_token(self) -> str:
        """Generate or reuse a JWT for APNs authentication.

        Tokens are valid for up to 60 minutes. We refresh at 50 minutes.
        """
        now = time.time()
        if self._token and now < self._token_expires:
            return self._token

        payload = {
            "iss": self._team_id,
            "iat": int(now),
        }
        self._token = jwt.encode(
            payload,
            self._private_key,
            algorithm="ES256",
            headers={
                "alg": "ES256",
                "kid": self._key_id,
            },
        )
        self._token_expires = now + 3000  # refresh after 50 minutes
        return self._token

    def _build_payload(self, notification: Notification) -> dict[str, Any]:
        """Build the APNs JSON payload based on urgency."""
        aps: dict[str, Any] = {
            "alert": {
                "title": notification.title,
                "body": notification.body,
            },
        }

        match notification.urgency:
            case Urgency.INFORMATIONAL:
                aps["interruption-level"] = "passive"
            case Urgency.IMPORTANT:
                aps["sound"] = "default"
                aps["interruption-level"] = "active"
            case Urgency.URGENT:
                aps["sound"] = {"critical": 1, "name": "default", "volume": 1.0}
                aps["interruption-level"] = "critical"

        return {
            "aps": aps,
            "notification_id": notification.notification_id,
        }

    async def deliver(self, notification: Notification) -> None:
        """Deliver notification to all registered iOS devices."""
        raw_tokens: dict[bytes | str, bytes | str] = await self._redis.hgetall(DEVICE_TOKENS_KEY)
        if not raw_tokens:
            logger.debug("APNsChannelAdapter: no registered devices, skipping")
            return

        client = await self._ensure_client()
        token = self._get_auth_token()
        payload = self._build_payload(notification)
        payload_bytes = json.dumps(payload)

        priority = "5" if notification.urgency == Urgency.INFORMATIONAL else "10"
        # Expiration: informational = immediate, others = 24h TTL
        expiration = (
            "0" if notification.urgency == Urgency.INFORMATIONAL else str(int(time.time()) + 86400)
        )
        headers: dict[str, str] = {
            "authorization": f"bearer {token}",
            "apns-topic": self._bundle_id,
            "apns-push-type": "alert",
            "apns-priority": priority,
            "apns-expiration": expiration,
        }
        # Collapse-id lets APNs coalesce retries/duplicates on the device
        if notification.notification_id:
            headers["apns-collapse-id"] = notification.notification_id[:64]

        async def _send_to_device(device_token_raw: bytes | str) -> None:
            device_token = (
                device_token_raw.decode()
                if isinstance(device_token_raw, bytes)
                else device_token_raw
            )
            url = f"{self._base_url}/3/device/{device_token}"
            try:
                resp = await client.post(url, content=payload_bytes, headers=headers)
                if resp.status_code == 410:
                    await self._redis.hdel(DEVICE_TOKENS_KEY, device_token)
                    logger.info("Pruned stale APNs token {}...", device_token[:8])
                elif resp.status_code != 200:
                    logger.warning(
                        "APNs delivery failed for token {}: {} {}",
                        device_token[:8],
                        resp.status_code,
                        resp.text,
                    )
                else:
                    logger.info(
                        "APNs notification sent (token={}..., urgency={})",
                        device_token[:8],
                        notification.urgency.value,
                    )
            except Exception as exc:
                logger.error("APNs delivery error for token {}: {}", device_token[:8], exc)

        await asyncio.gather(*[_send_to_device(tok) for tok in raw_tokens])
