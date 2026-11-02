"""Web Push delivery: each IMPORTANT/URGENT notification, encrypted, to every subscription."""

import base64
import json
import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import fakeredis
import http_ece
import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from loguru import logger

from core.notifications.adapters.webpush import (
    MAX_LOGGED_HOST,
    MAX_PAYLOAD_BYTES,
    WebPushChannelAdapter,
)
from core.notifications.devices import WEB_PUSH_PLATFORM, endpoint_field
from core.notifications.schema import Notification, Urgency
from core.notifications.vapid import VapidKeys, generate_private_pem, load_keys
from shared.streams import DEVICE_TOKENS_KEY

SUBJECT = "mailto:you@example.com"
APPLE = "https://web.push.apple.com/QGuQyavXutnMH8"
FCM_ORIGIN = "https://fcm.googleapis.com"
FCM = f"{FCM_ORIGIN}/fcm/send/dQw4w9WgXcQ"
MOZILLA = "https://updates.push.services.mozilla.com/wpush/v2/gAAAAABnZ"
WINDOWS = "https://wns2-par02p.notify.windows.com/w/?token=BQYAAAB"

KEYS = load_keys(generate_private_pem())


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _host(url: str) -> str:
    """The push host a log line may name, parsed rather than spelled out as a literal."""
    return urlsplit(url).hostname or ""


@dataclass
class Browser:
    """A subscribed browser: the receiver key and auth secret it subscribed with."""

    endpoint: str
    key: ec.EllipticCurvePrivateKey = field(
        default_factory=lambda: ec.generate_private_key(ec.SECP256R1())
    )
    auth: bytes = field(default_factory=lambda: os.urandom(16))

    @property
    def device_field(self) -> str:
        return endpoint_field(self.endpoint)

    @property
    def p256dh(self) -> str:
        point = self.key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        return _b64url(point)

    def row(self) -> str:
        return json.dumps(
            {
                "platform": WEB_PUSH_PLATFORM,
                "endpoint": self.endpoint,
                "keys": {"p256dh": self.p256dh, "auth": _b64url(self.auth)},
                "identity": "sir",
                "registered_at": "2026-10-29T00:00:00Z",
                "user_agent": "Mozilla/5.0 (iPhone)",
            }
        )

    def plaintext(self, request: httpx.Request) -> bytes:
        decrypted: bytes = http_ece.decrypt(
            request.content, private_key=self.key, auth_secret=self.auth, version="aes128gcm"
        )
        return decrypted

    def payload(self, request: httpx.Request) -> dict[str, Any]:
        decoded: dict[str, Any] = json.loads(self.plaintext(request))
        return decoded


class PushService:
    """The push services: records each POST and answers it with ``status``."""

    def __init__(self, status: int = 201, *, unreachable: str | None = None) -> None:
        self.status = status
        self.unreachable = unreachable
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == self.unreachable:
            # httpx's own messages can quote the URL; the adapter must not repeat them.
            raise httpx.ConnectError(f"cannot reach {request.url}", request=request)
        self.requests.append(request)
        return httpx.Response(self.status, text='{"reason":"BadDeviceToken"}')

    def to(self, endpoint: str) -> list[httpx.Request]:
        return [r for r in self.requests if str(r.url) == endpoint]


@pytest.fixture
async def redis() -> AsyncIterator[fakeredis.FakeAsyncRedis]:
    client = fakeredis.FakeAsyncRedis()
    yield client
    await client.aclose()


@pytest.fixture
def logs() -> Iterator[list[str]]:
    messages: list[str] = []
    sink_id = logger.add(messages.append, level="DEBUG", format="{level} {message}")
    yield messages
    logger.remove(sink_id)


async def _store(redis: fakeredis.FakeAsyncRedis, *browsers: Browser) -> None:
    for browser in browsers:
        await redis.hset(DEVICE_TOKENS_KEY, browser.device_field, browser.row())


async def _deliver(
    redis: fakeredis.FakeAsyncRedis,
    service: PushService,
    notification: Notification,
    keys: VapidKeys = KEYS,
) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as client:
        adapter = WebPushChannelAdapter(redis=redis, keys=keys, subject=SUBJECT, client=client)
        await adapter.deliver(notification)
        await adapter.close()


def _vapid_claims(request: httpx.Request, audience: str) -> dict[str, Any]:
    """The push's VAPID claims, the JWT verified against KEYS and ``audience``."""
    scheme, _, credentials = request.headers["Authorization"].partition(" ")
    assert scheme == "vapid"
    params = dict(part.split("=", 1) for part in credentials.split(", "))
    assert params["k"] == KEYS.public_key
    public = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), _b64url_decode(KEYS.public_key)
    )
    claims: dict[str, Any] = jwt.decode(
        params["t"], public, algorithms=["ES256"], audience=audience
    )
    return claims


def _notification(urgency: Urgency = Urgency.URGENT, **kwargs: Any) -> Notification:
    fields: dict[str, Any] = {"title": "Front door", "body": "It is open", "source": "test"}
    fields.update(kwargs)
    return Notification(urgency=urgency, **fields)


async def test_delivers_an_urgent_notification(redis: fakeredis.FakeAsyncRedis) -> None:
    browser = Browser(APPLE)
    await _store(redis, browser)
    service = PushService()
    notification = _notification(Urgency.URGENT)

    await _deliver(redis, service, notification)

    assert len(service.requests) == 1
    request = service.requests[0]
    assert request.method == "POST"
    assert str(request.url) == APPLE
    assert request.headers["Urgency"] == "high"
    assert request.headers["TTL"] == "3600"
    assert request.headers["Content-Encoding"] == "aes128gcm"
    assert request.headers["Content-Type"] == "application/octet-stream"
    assert _vapid_claims(request, "https://web.push.apple.com")["sub"] == SUBJECT
    assert browser.payload(request) == {
        "title": "Front door",
        "body": "It is open",
        "urgency": "urgent",
        "id": notification.notification_id,
        "action_id": None,
    }


async def test_important_is_normal_for_a_day(redis: fakeredis.FakeAsyncRedis) -> None:
    browser = Browser(APPLE)
    await _store(redis, browser)
    service = PushService()

    await _deliver(redis, service, _notification(Urgency.IMPORTANT))

    request = service.requests[0]
    assert request.headers["Urgency"] == "normal"
    assert request.headers["TTL"] == "86400"
    assert browser.payload(request)["urgency"] == "important"


async def test_signs_each_push_for_its_own_service(redis: fakeredis.FakeAsyncRedis) -> None:
    await _store(redis, Browser(APPLE), Browser(FCM))
    service = PushService()

    await _deliver(redis, service, _notification())

    for endpoint, origin in ((APPLE, "https://web.push.apple.com"), (FCM, FCM_ORIGIN)):
        (request,) = service.to(endpoint)
        assert _vapid_claims(request, origin)["aud"] == origin


async def test_informational_is_not_supported(redis: fakeredis.FakeAsyncRedis) -> None:
    adapter = WebPushChannelAdapter(redis=redis, keys=KEYS, subject=SUBJECT)
    assert not adapter.supports_urgency(Urgency.INFORMATIONAL)
    assert adapter.supports_urgency(Urgency.IMPORTANT)
    assert adapter.supports_urgency(Urgency.URGENT)


async def test_carries_the_pending_action_id(redis: fakeredis.FakeAsyncRedis) -> None:
    browser = Browser(APPLE)
    await _store(redis, browser)
    service = PushService()

    await _deliver(redis, service, _notification(metadata={"pending_action_id": "abc-123"}))

    assert browser.payload(service.requests[0])["action_id"] == "abc-123"


async def test_ignores_a_non_string_action_id(redis: fakeredis.FakeAsyncRedis) -> None:
    browser = Browser(APPLE)
    await _store(redis, browser)
    service = PushService()

    await _deliver(redis, service, _notification(metadata={"pending_action_id": 7}))

    assert browser.payload(service.requests[0])["action_id"] is None


@pytest.mark.parametrize("char", ["x", "é", '"'])  # one byte, two in UTF-8, two once escaped
async def test_trims_a_long_body_to_fit_the_push_service(
    redis: fakeredis.FakeAsyncRedis, char: str
) -> None:
    browser = Browser(APPLE)
    await _store(redis, browser)
    service = PushService()

    await _deliver(redis, service, _notification(body=char * 10_000))

    plaintext = browser.plaintext(service.requests[0])
    assert len(plaintext) <= MAX_PAYLOAD_BYTES
    assert len(plaintext) > MAX_PAYLOAD_BYTES - 8  # shortened to fit, not to nothing
    body = json.loads(plaintext)["body"]
    assert body.endswith("…")
    assert body[:-1] == char * (len(body) - 1)


async def test_trims_a_long_title_once_the_body_is_cut(redis: fakeredis.FakeAsyncRedis) -> None:
    browser = Browser(APPLE)
    await _store(redis, browser)
    service = PushService()

    await _deliver(redis, service, _notification(title="x" * 10_000))

    plaintext = browser.plaintext(service.requests[0])
    assert len(plaintext) <= MAX_PAYLOAD_BYTES
    assert len(plaintext) > MAX_PAYLOAD_BYTES - 8
    payload = json.loads(plaintext)
    assert payload["body"] == "…"  # the body gives way first
    assert payload["title"].endswith("…")
    assert payload["title"][:-1] == "x" * (len(payload["title"]) - 1)


@pytest.mark.parametrize("status", [404, 410])
async def test_prunes_a_dead_subscription(redis: fakeredis.FakeAsyncRedis, status: int) -> None:
    browser = Browser(APPLE)
    await _store(redis, browser)

    await _deliver(redis, PushService(status), _notification())

    assert not await redis.hexists(DEVICE_TOKENS_KEY, browser.device_field)


@pytest.mark.parametrize("status", [400, 403, 413, 429, 500])
async def test_keeps_the_row_on_other_failures(
    redis: fakeredis.FakeAsyncRedis, logs: list[str], status: int
) -> None:
    browser = Browser(APPLE)
    await _store(redis, browser)

    await _deliver(redis, PushService(status), _notification())

    assert await redis.hexists(DEVICE_TOKENS_KEY, browser.device_field)
    assert any("WARNING" in line and str(status) in line for line in logs)


async def test_one_failure_does_not_stop_the_rest(redis: fakeredis.FakeAsyncRedis) -> None:
    apple, fcm = Browser(APPLE), Browser(FCM)
    await _store(redis, apple, fcm)
    service = PushService(unreachable=APPLE)

    await _deliver(redis, service, _notification())

    assert len(service.to(FCM)) == 1
    assert fcm.payload(service.to(FCM)[0])["title"] == "Front door"
    # A connection error is not a dead subscription.
    assert await redis.hexists(DEVICE_TOKENS_KEY, apple.device_field)


async def test_skips_apns_and_unreadable_rows(redis: fakeredis.FakeAsyncRedis) -> None:
    await redis.hset(DEVICE_TOKENS_KEY, "abc123", json.dumps({"platform": "ios"}))
    await redis.hset(DEVICE_TOKENS_KEY, "def456", b"garbage")
    service = PushService()

    await _deliver(redis, service, _notification())

    assert service.requests == []


@pytest.mark.parametrize("platform", [["web_push"], ["ios"], {"a": 1}])
async def test_a_row_whose_platform_is_not_a_string_stops_nothing(
    redis: fakeredis.FakeAsyncRedis, platform: Any
) -> None:
    """An outside writer's corrupt row is skipped; the good row beside it still gets its push."""
    browser = Browser(APPLE)
    await _store(redis, browser)
    await redis.hset(DEVICE_TOKENS_KEY, "corrupt", json.dumps({"platform": platform}))
    service = PushService()

    await _deliver(redis, service, _notification())

    assert [str(request.url) for request in service.requests] == [APPLE]
    assert browser.payload(service.to(APPLE)[0])["title"] == "Front door"


@pytest.mark.parametrize(
    "endpoint",
    [
        pytest.param("https://alfred.example.com/push/QGuQyavXutnMH8", id="off-allowlist"),
        pytest.param("http://web.push.apple.com/QGuQyavXutnMH8", id="plain-http"),
        pytest.param("https://web.push.apple.com:8443/QGuQyavXutnMH8", id="port"),
        pytest.param(f"https://{'a' * 80}.example.com/push/QGuQyavXutnMH8", id="long-host"),
    ],
)
async def test_skips_a_stored_row_the_allowlist_refuses(
    redis: fakeredis.FakeAsyncRedis, logs: list[str], endpoint: str
) -> None:
    """A row registration would have refused, written by anything else, gets no POST.

    It is kept (nothing proves the push service dropped it) and named only by its field's
    prefix and its host, quoted and cut to ``MAX_LOGGED_HOST``; the good row beside it still
    gets its push.
    """
    planted, good = Browser(endpoint), Browser(FCM)
    await _store(redis, planted, good)
    service = PushService()

    await _deliver(redis, service, _notification())

    assert [str(request.url) for request in service.requests] == [FCM]
    assert await redis.hexists(DEVICE_TOKENS_KEY, planted.device_field)
    [warning] = [line for line in logs if line.startswith("WARNING")]
    assert planted.device_field[:12] in warning
    host = urlsplit(endpoint).hostname or ""
    assert repr(host[:MAX_LOGGED_HOST]) in warning
    assert len(host) <= MAX_LOGGED_HOST or host not in warning
    for line in logs:
        for secret in (endpoint, endpoint.split("/", 3)[3], planted.p256dh, _b64url(planted.auth)):
            assert secret not in line


async def test_skips_a_stored_row_whose_keys_are_not_a_subscription(
    redis: fakeredis.FakeAsyncRedis, logs: list[str]
) -> None:
    """A p256dh that is not a P-256 point, or no keys at all, gets no POST either."""
    bad_point, keyless, good = Browser(APPLE), Browser(MOZILLA), Browser(FCM)
    await _store(redis, good)
    row = json.loads(bad_point.row())
    row["keys"]["p256dh"] = _b64url(b"\x04" + b"\x00" * 64)
    await redis.hset(DEVICE_TOKENS_KEY, bad_point.device_field, json.dumps(row))
    row = json.loads(keyless.row())
    del row["keys"]
    await redis.hset(DEVICE_TOKENS_KEY, keyless.device_field, json.dumps(row))
    service = PushService()

    await _deliver(redis, service, _notification())

    assert [str(request.url) for request in service.requests] == [FCM]
    warnings = [line for line in logs if line.startswith("WARNING")]
    assert len(warnings) == 2
    assert any(repr(_host(APPLE)) in line for line in warnings)
    assert any(repr(_host(MOZILLA)) in line for line in warnings)


async def test_never_logs_the_endpoint_or_keys(
    redis: fakeredis.FakeAsyncRedis, logs: list[str]
) -> None:
    sent, gone, failing, unreachable = (
        Browser(APPLE),
        Browser(FCM),
        Browser(MOZILLA),
        Browser(WINDOWS),
    )
    await _store(redis, sent, gone, failing, unreachable)

    def answer(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == WINDOWS:
            raise httpx.ConnectError(f"cannot reach {url}", request=request)
        status = {APPLE: 201, FCM: 410, MOZILLA: 500}[url]
        return httpx.Response(status, text='{"reason":"InternalServerError"}')

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        adapter = WebPushChannelAdapter(redis=redis, keys=KEYS, subject=SUBJECT, client=client)
        await adapter.deliver(_notification())

    assert logs  # each outcome logged something
    secrets = [KEYS.private_pem.splitlines()[1]]
    for browser in (sent, gone, failing, unreachable):
        path = browser.endpoint.split("/", 3)[3]
        secrets += [browser.endpoint, path, browser.p256dh, _b64url(browser.auth)]
    for line in logs:
        for secret in secrets:
            assert secret not in line
    # What is logged instead: the field's first 12 characters and the push host.
    assert any(sent.device_field[:12] in line and _host(APPLE) in line for line in logs)
    assert any(unreachable.device_field[:12] in line and "ERROR" in line for line in logs)


async def test_builds_its_own_http2_client_and_closes_it(
    redis: fakeredis.FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    browser = Browser(APPLE)
    await _store(redis, browser)
    service = PushService()
    built: list[tuple[dict[str, Any], httpx.AsyncClient]] = []
    real_client = httpx.AsyncClient

    def build(**kwargs: Any) -> httpx.AsyncClient:
        client = real_client(transport=httpx.MockTransport(service))
        built.append((kwargs, client))
        return client

    monkeypatch.setattr(httpx, "AsyncClient", build)
    adapter = WebPushChannelAdapter(redis=redis, keys=KEYS, subject=SUBJECT)

    await adapter.deliver(_notification())
    await adapter.deliver(_notification())
    await adapter.close()

    assert len(service.requests) == 2
    assert [kwargs for kwargs, _ in built] == [{"http2": True, "timeout": 10.0}]
    assert built[0][1].is_closed


async def test_leaves_a_given_client_open(redis: fakeredis.FakeAsyncRedis) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(PushService())) as client:
        adapter = WebPushChannelAdapter(redis=redis, keys=KEYS, subject=SUBJECT, client=client)
        await adapter.close()
        assert not client.is_closed
