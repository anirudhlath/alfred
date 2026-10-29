"""The push-device vocabulary: what a Web Push subscription may be, and how rows read back."""

import base64
import hashlib
import os

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import ValidationError

from core.notifications.devices import (
    WebPushKeys,
    WebPushSubscription,
    decode_device,
    endpoint_field,
    is_web_push,
    push_host,
)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _point() -> bytes:
    public = ec.generate_private_key(ec.SECP256R1()).public_key()
    return public.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)


APPLE = "https://web.push.apple.com/QGuQyavXutnMH8"
P256DH = _b64url(_point())
AUTH = _b64url(os.urandom(16))
KEYS = {"p256dh": P256DH, "auth": AUTH}
OFF_CURVE = _b64url(b"\x04" + b"\x01" * 64)  # 65 bytes, the uncompressed tag, not on P-256
SHORT_AUTH = _b64url(os.urandom(15))


@pytest.mark.parametrize(
    "host",
    [
        "web.push.apple.com",
        "fcm.googleapis.com",
        "updates.push.services.mozilla.com",
        "wns2-par02p.notify.windows.com",
    ],
)
def test_accepts_each_push_service(host: str) -> None:
    endpoint = f"https://{host}/QGuQyavXutnMH8"
    sub = WebPushSubscription.model_validate({"endpoint": endpoint, "keys": KEYS})
    assert sub.endpoint == endpoint
    assert sub.keys == WebPushKeys(p256dh=P256DH, auth=AUTH)


def test_accepts_the_default_port_spelled_out() -> None:
    endpoint = "https://web.push.apple.com:443/QGuQyavXutnMH8"
    assert WebPushSubscription.model_validate({"endpoint": endpoint, "keys": KEYS})


def test_ignores_expiration_time() -> None:
    sub = WebPushSubscription.model_validate(
        {"endpoint": APPLE, "expirationTime": None, "keys": KEYS}
    )
    assert sub.model_dump() == {"endpoint": APPLE, "keys": KEYS}


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://web.push.apple.com/x",
        "https://192.168.1.20/x",
        "https://evilpush.apple.com.example.com/x",
        "https://notpush.apple.com/x",
        "https://user@web.push.apple.com/x",
        "https://web.push.apple.com:8443/x",
        "https://web.push.apple.com:notaport/x",
        "https://[web.push.apple.com/x",
        "https://[::1]/x",
        "https://web.push.apple.com/" + "a" * 2048,
        # A backslash is a path separator to a browser, so the host here is evil.example.com.
        "https://evil.example.com\\.push.apple.com/x",
        # urlsplit drops tabs and newlines before parsing; what was checked must be what is kept.
        "https://web.push.apple.com/a\nb",
        "",
    ],
)
def test_refuses_an_endpoint_off_the_push_services(endpoint: str) -> None:
    with pytest.raises(ValidationError):
        WebPushSubscription.model_validate({"endpoint": endpoint, "keys": KEYS})


@pytest.mark.parametrize(
    ("endpoint", "keys"),
    [
        ("https://notpush.apple.com/QGuQyavXutnMH8", KEYS),
        # urllib's own error here reads "'QGuQyavXutnMH8' does not appear to be an IPv4…".
        ("https://[QGuQyavXutnMH8]/x", KEYS),
        (APPLE, {"p256dh": OFF_CURVE, "auth": AUTH}),
        (APPLE, {"p256dh": P256DH, "auth": SHORT_AUTH}),
        (APPLE, {"p256dh": P256DH, "auth": "not base64!" + AUTH}),
    ],
)
def test_a_refusal_never_echoes_the_credential(endpoint: str, keys: dict[str, str]) -> None:
    with pytest.raises(ValidationError) as caught:
        WebPushSubscription.model_validate({"endpoint": endpoint, "keys": keys})
    message = str(caught.value)
    for credential in (endpoint, "QGuQyavXutnMH8", keys["p256dh"], keys["auth"], AUTH):
        assert credential not in message


def test_strips_padding() -> None:
    keys = WebPushKeys.model_validate({"p256dh": P256DH + "=", "auth": AUTH + "=="})
    assert keys.p256dh == P256DH
    assert keys.auth == AUTH


def test_refuses_a_p256dh_that_is_not_a_point() -> None:
    with pytest.raises(ValidationError, match="p256dh"):
        WebPushKeys.model_validate({"p256dh": OFF_CURVE, "auth": AUTH})


def test_refuses_a_compressed_p256dh() -> None:
    public = ec.generate_private_key(ec.SECP256R1()).public_key()
    compressed = _b64url(public.public_bytes(Encoding.X962, PublicFormat.CompressedPoint))
    with pytest.raises(ValidationError, match="p256dh"):
        WebPushKeys.model_validate({"p256dh": compressed, "auth": AUTH})


def test_refuses_a_key_that_is_not_base64url() -> None:
    with pytest.raises(ValidationError, match="auth"):
        WebPushKeys.model_validate({"p256dh": P256DH, "auth": "not base64!" + AUTH})


def test_refuses_an_auth_of_the_wrong_length() -> None:
    with pytest.raises(ValidationError, match="auth"):
        WebPushKeys.model_validate({"p256dh": P256DH, "auth": SHORT_AUTH})


def test_endpoint_field_is_sha256_hex() -> None:
    assert endpoint_field(APPLE) == hashlib.sha256(APPLE.encode()).hexdigest()


def test_push_host() -> None:
    assert push_host(APPLE) == "web.push.apple.com"


def test_decode_device_tolerates_garbage() -> None:
    assert decode_device(b"not json") is None
    assert decode_device(b"[1]") is None
    assert decode_device(b"\xff") is None
    assert decode_device(b'{"platform":"ios"}') == {"platform": "ios"}
    assert decode_device('{"platform":"web_push"}') == {"platform": "web_push"}


def test_is_web_push() -> None:
    assert is_web_push({"platform": "web_push"})
    assert not is_web_push({"platform": "ios"})
    assert not is_web_push({})
    assert not is_web_push(None)
