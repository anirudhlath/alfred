"""Push-device vocabulary — what a registered device is, shared by registration and delivery.

Every push device lives in one Redis hash, ``DEVICE_TOKENS_KEY`` (``alfred:push:devices``),
one JSON object per field. APNs devices are keyed by their device token; a Web Push device
is keyed by ``endpoint_field(endpoint)``, the SHA-256 hex of its endpoint, and its value is
``{platform: "web_push", endpoint, keys: {p256dh, auth}, identity, registered_at,
user_agent}``.

A Web Push endpoint is a URL the browser hands us and we later POST to, so it is checked
against the known push services before it is stored: an endpoint anywhere else would make
Alfred send requests wherever a client pointed it. The endpoint, ``p256dh`` and ``auth``
are credentials — anyone holding them can push to the device — so nothing here logs them,
and a ``ValidationError``'s message never quotes them (``hide_input_in_errors``). An APNs
device token is one too, and nothing here logs any of it. Both sit in the URL of the
request that pushes to the device, which httpx logs: ``core/notifications/http_logs.py``
cuts those URLs to the host.

The hash holds at most ``MAX_DEVICES_PER_FAMILY`` rows of each family, APNs and Web Push,
counted apart: ``store_device()`` evicts the oldest of the newcomer's own family, so a
burst of browsers never costs an iPhone, nor the reverse. The cap is what bounds a stolen
session's writes, since neither push service tells us a made-up device is fake in a way
the adapters prune on (Apple answers 400, and the APNs adapter prunes only on 410).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from typing import TYPE_CHECKING, Any, Final, Literal, get_args
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric import ec
from loguru import logger
from pydantic import BaseModel, ConfigDict, field_validator
from redis.exceptions import WatchError

from shared.streams import DEVICE_TOKENS_KEY, decode_stream_value

if TYPE_CHECKING:
    from collections.abc import Mapping

    from shared.types import AioRedis

WEB_PUSH_PLATFORM: Final = "web_push"
ApnsPlatform = Literal["ios", "ipados", "macos"]
APNS_PLATFORMS: Final = frozenset(get_args(ApnsPlatform))

# The two kinds of push device the hash holds; each is capped on its own.
DeviceFamily = Literal["apns", "web_push"]

# The browsers' push services. A host matches when it equals one or ends with "." + one,
# so the match is on a label boundary: push.apple.com covers web.push.apple.com, never
# notpush.apple.com.
PUSH_SERVICE_HOSTS: Final = (
    "push.apple.com",
    "fcm.googleapis.com",
    "push.services.mozilla.com",
    "notify.windows.com",
)

MAX_ENDPOINT_LEN: Final = 2048
# Rows kept per family; the eleventh of a family evicts that family's oldest.
MAX_DEVICES_PER_FAMILY: Final = 10
# Transactions ``store_device()`` tries before it gives up on a contended hash.
STORE_ATTEMPTS: Final = 5

_P256_POINT_LEN: Final = 65  # X9.62 uncompressed: 0x04 || X (32) || Y (32)
_AUTH_LEN: Final = 16

_B64URL: Final = re.compile(r"[A-Za-z0-9_-]+")
# Visible ASCII only. urlsplit silently drops tabs and newlines before it parses, so an
# endpoint carrying them would be checked as one URL and stored as another.
_VISIBLE_ASCII: Final = re.compile(r"[\x21-\x7e]+")
# A DNS name. Anything else in the host (a backslash, which a browser reads as a path
# separator) could make urlsplit and the HTTP client disagree about where the request goes.
_DNS_NAME: Final = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*")


def decode_b64url(value: str, name: str) -> tuple[str, bytes]:
    """Return ``value`` unpadded and its bytes; padding is accepted, anything else refused."""
    stripped = value.rstrip("=")
    if not _B64URL.fullmatch(stripped):
        raise ValueError(f"{name} must be base64url")
    try:
        raw = base64.urlsafe_b64decode(stripped + "=" * (-len(stripped) % 4))
    except binascii.Error as exc:
        raise ValueError(f"{name} must be base64url") from exc
    return stripped, raw


def is_push_service(host: str) -> bool:
    """Whether a lowercase hostname is on one of ``PUSH_SERVICE_HOSTS``."""
    return any(host == known or host.endswith("." + known) for known in PUSH_SERVICE_HOSTS)


class WebPushKeys(BaseModel):
    """The subscription's encryption keys (RFC 8291), base64url and unpadded once validated."""

    model_config = ConfigDict(hide_input_in_errors=True)

    p256dh: str
    auth: str

    @field_validator("p256dh")
    @classmethod
    def _check_p256dh(cls, value: str) -> str:
        stripped, raw = decode_b64url(value, "p256dh")
        # from_encoded_point also takes a compressed point; RFC 8291 sends the uncompressed one.
        if len(raw) != _P256_POINT_LEN or raw[0] != 0x04:
            raise ValueError("p256dh must be an uncompressed P-256 point")
        try:
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
        except ValueError as exc:
            raise ValueError("p256dh must be an uncompressed P-256 point") from exc
        return stripped

    @field_validator("auth")
    @classmethod
    def _check_auth(cls, value: str) -> str:
        stripped, raw = decode_b64url(value, "auth")
        if len(raw) != _AUTH_LEN:
            raise ValueError(f"auth must be {_AUTH_LEN} bytes")
        return stripped


class WebPushSubscription(BaseModel):
    """A browser's ``PushSubscription.toJSON()``, its endpoint on a known push service."""

    # toJSON() also carries expirationTime, which no browser sets to anything useful.
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)

    endpoint: str
    keys: WebPushKeys

    @field_validator("endpoint")
    @classmethod
    def _check_endpoint(cls, value: str) -> str:
        if len(value) > MAX_ENDPOINT_LEN:
            raise ValueError(f"endpoint must be at most {MAX_ENDPOINT_LEN} characters")
        if not _VISIBLE_ASCII.fullmatch(value):
            raise ValueError("endpoint must be visible ASCII")
        try:
            parts = urlsplit(value)
        except ValueError as exc:  # urllib's message can quote the host it could not read
            raise ValueError("endpoint is not a URL") from exc
        if parts.scheme != "https":
            raise ValueError("endpoint must be https")
        if "@" in parts.netloc:
            raise ValueError("endpoint must not carry userinfo")
        try:
            port = parts.port
        except ValueError as exc:  # urllib's message quotes the port it could not read
            raise ValueError("endpoint port is not a number") from exc
        if port not in (None, 443):
            raise ValueError("endpoint must use port 443")
        host = parts.hostname
        if host is None or not _DNS_NAME.fullmatch(host) or not is_push_service(host):
            raise ValueError("endpoint is not on a known push service")
        return value


def endpoint_field(endpoint: str) -> str:
    """The device hash field for a Web Push endpoint: the SHA-256 hex of its UTF-8 bytes."""
    return hashlib.sha256(endpoint.encode()).hexdigest()


def decode_device(value: bytes | str) -> dict[str, Any] | None:
    """A device row as a dict, or ``None`` for anything that is not a JSON object."""
    try:
        row = json.loads(value)
    except ValueError:  # JSONDecodeError and UnicodeDecodeError both
        return None
    return row if isinstance(row, dict) else None


def is_web_push(row: dict[str, Any] | None) -> bool:
    """Whether a decoded device row is a Web Push subscription."""
    return device_family(row) == "web_push"


def push_host(endpoint: str) -> str:
    """The endpoint's hostname — safe to log, unlike the endpoint itself."""
    return urlsplit(endpoint).hostname or ""


def device_family(row: dict[str, Any] | None) -> DeviceFamily | None:
    """Which family a decoded device row belongs to, or ``None`` for a row of neither.

    A ``platform`` that is not a string (a list or an object, which an outside writer could
    store) names no family: it is never hashed, so it cannot raise.
    """
    if row is None:
        return None
    platform = row.get("platform")
    if not isinstance(platform, str):
        return None
    if platform == WEB_PUSH_PLATFORM:
        return "web_push"
    return "apns" if platform in APNS_PLATFORMS else None


def evictions(
    rows: Mapping[bytes | str, bytes | str],
    field: str,
    family: DeviceFamily,
    cap: int = MAX_DEVICES_PER_FAMILY,
) -> list[str]:
    """The fields to delete so that ``field`` fits its family's cap, oldest first.

    Counts only the rows of ``family`` other than ``field``, and evicts the oldest of them
    down to ``cap - 1``, so ``field`` makes ``cap``. Re-registering a field of the same
    family evicts nothing: it was already one of the ``cap``. A field stored as the other
    family counts as new (the write replaces that row), so no field an APNs token and a
    Web Push hash can share slips past either cap. A row of neither family (not JSON, not
    an object, no known platform) is neither counted nor evicted. More than one only when
    the family is already over the cap, which this then heals.
    """
    stamped: list[tuple[str, str]] = []
    for raw_name, value in rows.items():
        name = decode_stream_value(raw_name)
        if name == field:
            continue
        row = decode_device(value)
        if row is not None and device_family(row) == family:
            # isoformat() in UTC sorts as text; a row with no stamp reads as the oldest.
            stamp = row.get("registered_at")
            stamped.append((stamp if isinstance(stamp, str) else "", name))
    excess = len(stamped) - cap + 1
    return [name for _, name in sorted(stamped)[:excess]] if excess > 0 else []


class DeviceStoreBusyError(RuntimeError):
    """Every attempt to store a device lost its transaction to a concurrent write."""


async def store_device(redis: AioRedis, field: str, value: str, family: DeviceFamily) -> list[str]:
    """Write one device row and evict its family's oldest past the cap, atomically.

    One transaction per attempt: WATCH the hash, read it, then MULTI the evictions and the
    write and EXEC. A write to the hash in between aborts the EXEC, and the next attempt
    reads and counts it, so concurrent registrations cannot overrun the cap. After
    ``STORE_ATTEMPTS`` aborted attempts this raises ``DeviceStoreBusyError``, nothing
    written. Returns the evicted fields.
    """
    for _ in range(STORE_ATTEMPTS):
        async with redis.pipeline(transaction=True) as pipe:
            await pipe.watch(DEVICE_TOKENS_KEY)
            evicted = evictions(await pipe.hgetall(DEVICE_TOKENS_KEY), field, family)
            pipe.multi()  # type: ignore[no-untyped-call]  # unannotated in redis-py
            if evicted:
                pipe.hdel(DEVICE_TOKENS_KEY, *evicted)
            pipe.hset(DEVICE_TOKENS_KEY, field, value)
            try:
                await pipe.execute()
            except WatchError:
                continue
        for name in evicted:
            if family == "web_push":
                logger.info("Evicted the oldest web push subscription ({}…)", name[:12])
            else:  # a device token is a credential: not even a prefix of it
                logger.info("Evicted the oldest APNs device token")
        return evicted
    logger.warning("Device registration gave up after {} contended attempts", STORE_ATTEMPTS)
    raise DeviceStoreBusyError(f"device store contended {STORE_ATTEMPTS} times")
