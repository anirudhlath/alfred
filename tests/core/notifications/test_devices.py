"""The push-device vocabulary: what a Web Push subscription may be, and how rows read back."""

import base64
import hashlib
import itertools
import json
import os
from collections.abc import Iterable, Iterator
from typing import Any

import fakeredis
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from loguru import logger
from pydantic import ValidationError

from core.notifications import devices
from core.notifications.devices import (
    MAX_DEVICES_PER_FAMILY,
    STORE_ATTEMPTS,
    DeviceFamily,
    DeviceStoreBusyError,
    WebPushKeys,
    WebPushSubscription,
    decode_device,
    device_family,
    endpoint_field,
    evictions,
    is_web_push,
    push_host,
    store_device,
)
from shared.streams import DEVICE_TOKENS_KEY


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


# A platform an outside writer stored as something other than a string.
NOT_A_STRING: list[Any] = [["ios"], ["web_push"], {"a": 1}, 1, None]


@pytest.mark.parametrize("platform", NOT_A_STRING)
def test_is_web_push_refuses_a_platform_that_is_not_a_string(platform: Any) -> None:
    assert not is_web_push({"platform": platform})


# --- The storage policy: one cap per family, kept in one transaction -------------------

MAX = MAX_DEVICES_PER_FAMILY


def _stamp(n: int) -> str:
    return f"2026-10-{n + 1:02d}T00:00:00+00:00"


def _web(n: int) -> tuple[str, str]:
    """A Web Push row, registered on day ``n + 1``, under its endpoint's field."""
    row = {"platform": "web_push", "endpoint": f"{APPLE}-{n:02d}", "registered_at": _stamp(n)}
    return endpoint_field(f"{APPLE}-{n:02d}"), json.dumps(row)


def _apple(n: int, platform: str = "ios") -> tuple[str, str]:
    """An APNs row, registered on day ``n + 1``, under its device token."""
    row = {"platform": platform, "identity": "sir", "registered_at": _stamp(n)}
    return f"{n:02d}" * 16, json.dumps(row)


def _rows(*groups: Iterable[tuple[str, str]]) -> dict[bytes | str, bytes | str]:
    return {name: value for group in groups for name, value in group}


_MAKE = {"web_push": _web, "apns": _apple}
FAMILIES: tuple[DeviceFamily, ...] = ("web_push", "apns")


@pytest.mark.parametrize(
    ("row", "family"),
    [
        ({"platform": "web_push"}, "web_push"),
        ({"platform": "ios"}, "apns"),
        ({"platform": "ipados"}, "apns"),
        ({"platform": "macos"}, "apns"),
        ({"platform": "android"}, None),
        ({}, None),
        (None, None),
    ],
)
def test_device_family(row: dict[str, Any] | None, family: DeviceFamily | None) -> None:
    assert device_family(row) == family


@pytest.mark.parametrize("platform", NOT_A_STRING)
def test_a_platform_that_is_not_a_string_is_no_family(platform: Any) -> None:
    assert device_family({"platform": platform}) is None


@pytest.mark.parametrize("family", FAMILIES)
def test_evicts_nothing_below_the_cap(family: DeviceFamily) -> None:
    rows = _rows(_MAKE[family](n) for n in range(MAX - 1))

    assert evictions(rows, "newcomer", family) == []


@pytest.mark.parametrize("family", FAMILIES)
def test_evicts_the_oldest_of_its_family(family: DeviceFamily) -> None:
    # Stored newest first, so the answer cannot be riding the hash's order.
    rows = _rows(_MAKE[family](n) for n in reversed(range(MAX)))

    assert evictions(rows, "newcomer", family) == [_MAKE[family](0)[0]]


def test_a_family_never_evicts_the_other() -> None:
    rows = _rows((_web(n) for n in range(MAX)), (_apple(n) for n in range(MAX)))

    assert evictions(rows, "newcomer", "web_push") == [_web(0)[0]]
    assert evictions(rows, "newcomer", "apns") == [_apple(0)[0]]


def test_every_apple_platform_is_one_family() -> None:
    platforms = ("ios", "ipados", "macos")
    rows = _rows(_apple(n, platforms[n % 3]) for n in range(MAX))

    assert evictions(rows, "newcomer", "apns") == [_apple(0)[0]]


@pytest.mark.parametrize("family", FAMILIES)
def test_re_registering_at_the_cap_evicts_nothing(family: DeviceFamily) -> None:
    rows = _rows(_MAKE[family](n) for n in range(MAX))

    assert evictions(rows, _MAKE[family](5)[0], family) == []


def test_a_web_push_field_taken_by_an_apple_token_counts_as_new() -> None:
    """An APNs body whose token is a stored endpoint's hash still pays its family's cap."""
    shared, browser = _web(MAX)
    rows = _rows((_apple(n) for n in range(MAX)), [(shared, browser)])

    assert evictions(rows, shared, "apns") == [_apple(0)[0]]


def test_an_apple_token_taken_by_a_web_push_field_counts_as_new() -> None:
    shared, iphone = _apple(MAX)
    rows = _rows((_web(n) for n in range(MAX)), [(shared, iphone)])

    assert evictions(rows, shared, "web_push") == [_web(0)[0]]


@pytest.mark.parametrize("family", FAMILIES)
def test_re_registering_an_overrun_family_heals_it_around_the_field(
    family: DeviceFamily,
) -> None:
    rows = _rows(_MAKE[family](n) for n in range(MAX + 2))

    # 11 others: two go, and never the field being written.
    assert evictions(rows, _MAKE[family](0)[0], family) == [_MAKE[family](n)[0] for n in (1, 2)]


def test_heals_an_overrun_cap() -> None:
    rows = _rows(_web(n) for n in range(MAX + 2))

    assert evictions(rows, "newcomer", "web_push") == [_web(n)[0] for n in range(3)]


def test_a_row_without_a_stamp_reads_as_the_oldest() -> None:
    rows = _rows(_web(n) for n in range(1, MAX))
    rows["unstamped"] = json.dumps({"platform": "web_push"})

    assert evictions(rows, "newcomer", "web_push") == ["unstamped"]


@pytest.mark.parametrize(
    "corrupt",
    [
        b"not json",
        b"[1, 2]",
        b'"text"',
        b'{"identity": "sir"}',
        b"\xff",
        *(json.dumps({"platform": platform}).encode() for platform in NOT_A_STRING),
    ],
)
@pytest.mark.parametrize("family", FAMILIES)
def test_a_corrupt_row_neither_counts_nor_goes(corrupt: bytes, family: DeviceFamily) -> None:
    below = _rows(_MAKE[family](n) for n in range(MAX - 1))
    below["corrupt"] = corrupt
    assert evictions(below, "newcomer", family) == []

    full = _rows(_MAKE[family](n) for n in range(MAX))
    full["corrupt"] = corrupt
    assert evictions(full, "newcomer", family) == [_MAKE[family](0)[0]]


def test_reads_the_bytes_redis_returns() -> None:
    rows: dict[bytes | str, bytes | str] = {
        name.encode(): value.encode() for name, value in (_web(n) for n in range(MAX))
    }

    assert evictions(rows, "newcomer", "web_push") == [_web(0)[0]]


@pytest.fixture
def server() -> fakeredis.FakeServer:
    return fakeredis.FakeServer()


@pytest.fixture
def redis(server: fakeredis.FakeServer) -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis(server=server)


@pytest.fixture
def other(server: fakeredis.FakeServer) -> fakeredis.FakeRedis:
    """Another client on the same Redis: a second request, or the test looking in."""
    return fakeredis.FakeRedis(server=server, decode_responses=True)


@pytest.fixture
def logs() -> Iterator[list[str]]:
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="DEBUG")
    yield messages
    logger.remove(sink_id)


def _seed(other: fakeredis.FakeRedis, rows: Iterable[tuple[str, str]]) -> None:
    for name, value in rows:
        other.hset(DEVICE_TOKENS_KEY, name, value)


async def test_store_device_writes_the_row_and_evicts(
    redis: fakeredis.FakeAsyncRedis, other: fakeredis.FakeRedis
) -> None:
    _seed(other, (_web(n) for n in range(MAX)))
    name, value = _web(MAX)

    assert await store_device(redis, name, value, "web_push") == [_web(0)[0]]

    assert other.hget(DEVICE_TOKENS_KEY, name) == value
    assert not other.hexists(DEVICE_TOKENS_KEY, _web(0)[0])
    assert other.hlen(DEVICE_TOKENS_KEY) == MAX


async def test_store_device_counts_a_registration_that_lands_mid_write(
    redis: fakeredis.FakeAsyncRedis, other: fakeredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row written between the read and the write is seen on the retry, not overrun."""
    _seed(other, (_web(n) for n in range(MAX - 1)))
    real = devices.evictions
    reads: list[int] = []

    def racing(
        rows: dict[bytes | str, bytes | str], field: str, family: DeviceFamily, cap: int = MAX
    ) -> list[str]:
        reads.append(len(rows))
        if len(reads) == 1:
            other.hset(DEVICE_TOKENS_KEY, *_web(MAX - 1))  # the tenth, from another request
        return real(rows, field, family, cap)

    monkeypatch.setattr(devices, "evictions", racing)
    name, value = _web(MAX)

    assert await store_device(redis, name, value, "web_push") == [_web(0)[0]]

    assert reads == [MAX - 1, MAX]
    assert other.hlen(DEVICE_TOKENS_KEY) == MAX
    assert other.hexists(DEVICE_TOKENS_KEY, _web(MAX - 1)[0])
    assert other.hexists(DEVICE_TOKENS_KEY, name)


async def test_store_device_gives_up_loudly(
    redis: fakeredis.FakeAsyncRedis, other: fakeredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = devices.evictions
    racers = itertools.count()

    def always_racing(
        rows: dict[bytes | str, bytes | str], field: str, family: DeviceFamily, cap: int = MAX
    ) -> list[str]:
        other.hset(DEVICE_TOKENS_KEY, f"racer-{next(racers)}", "x")
        return real(rows, field, family, cap)

    monkeypatch.setattr(devices, "evictions", always_racing)
    name, value = _web(0)

    with pytest.raises(DeviceStoreBusyError):
        await store_device(redis, name, value, "web_push")

    assert next(racers) == STORE_ATTEMPTS
    assert not other.hexists(DEVICE_TOKENS_KEY, name)


async def test_store_device_logs_a_web_push_eviction_by_its_prefix(
    redis: fakeredis.FakeAsyncRedis, other: fakeredis.FakeRedis, logs: list[str]
) -> None:
    _seed(other, (_web(n) for n in range(MAX)))
    evicted = _web(0)[0]

    await store_device(redis, *_web(MAX), "web_push")

    joined = "\n".join(logs)
    assert f"Evicted the oldest web push subscription ({evicted[:12]}…)" in joined
    assert evicted[12:] not in joined


async def test_store_device_never_logs_an_apple_token(
    redis: fakeredis.FakeAsyncRedis, other: fakeredis.FakeRedis, logs: list[str]
) -> None:
    _seed(other, (_apple(n) for n in range(MAX)))
    name, value = _apple(MAX)

    evicted = await store_device(redis, name, value, "apns")

    joined = "\n".join(logs)
    assert evicted == [_apple(0)[0]]
    assert "Evicted the oldest APNs device token" in joined
    for token in (*evicted, name):
        assert token[:12] not in joined
