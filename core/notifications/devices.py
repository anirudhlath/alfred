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
and a ``ValidationError``'s message never quotes them (``hide_input_in_errors``).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from typing import Any, Final
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import BaseModel, ConfigDict, field_validator

WEB_PUSH_PLATFORM: Final = "web_push"
APNS_PLATFORMS: Final = frozenset({"ios", "ipados", "macos"})

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
MAX_WEB_PUSH_SUBSCRIPTIONS: Final = 10

_P256_POINT_LEN: Final = 65  # X9.62 uncompressed: 0x04 || X (32) || Y (32)
_AUTH_LEN: Final = 16

_B64URL: Final = re.compile(r"[A-Za-z0-9_-]+")
# Visible ASCII only. urlsplit silently drops tabs and newlines before it parses, so an
# endpoint carrying them would be checked as one URL and stored as another.
_VISIBLE_ASCII: Final = re.compile(r"[\x21-\x7e]+")
# A DNS name. Anything else in the host (a backslash, which a browser reads as a path
# separator) could make urlsplit and the HTTP client disagree about where the request goes.
_DNS_NAME: Final = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*")


def _decode_b64url(value: str, name: str) -> tuple[str, bytes]:
    """Return ``value`` unpadded and its bytes; padding is accepted, anything else refused."""
    stripped = value.rstrip("=")
    if not _B64URL.fullmatch(stripped):
        raise ValueError(f"{name} must be base64url")
    try:
        raw = base64.urlsafe_b64decode(stripped + "=" * (-len(stripped) % 4))
    except binascii.Error as exc:
        raise ValueError(f"{name} must be base64url") from exc
    return stripped, raw


def _is_push_service(host: str) -> bool:
    return any(host == known or host.endswith("." + known) for known in PUSH_SERVICE_HOSTS)


class WebPushKeys(BaseModel):
    """The subscription's encryption keys (RFC 8291), base64url and unpadded once validated."""

    model_config = ConfigDict(hide_input_in_errors=True)

    p256dh: str
    auth: str

    @field_validator("p256dh")
    @classmethod
    def _check_p256dh(cls, value: str) -> str:
        stripped, raw = _decode_b64url(value, "p256dh")
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
        stripped, raw = _decode_b64url(value, "auth")
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
        parts = urlsplit(value)
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
        if host is None or not _DNS_NAME.fullmatch(host) or not _is_push_service(host):
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
    return row is not None and row.get("platform") == WEB_PUSH_PLATFORM


def push_host(endpoint: str) -> str:
    """The endpoint's hostname — safe to log, unlike the endpoint itself."""
    return urlsplit(endpoint).hostname or ""
