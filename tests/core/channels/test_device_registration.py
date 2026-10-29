import base64
import json
import os
from collections.abc import Iterable, Iterator
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import fakeredis
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from loguru import logger

from core.channels.web_server import _init_webpush_adapter, create_app
from core.identity.auth_routes import _MAX_USER_AGENT_LEN
from core.notifications.adapters.webpush import WebPushChannelAdapter
from core.notifications.channels import ChannelRegistry
from core.notifications.devices import (
    MAX_WEB_PUSH_SUBSCRIPTIONS,
    WEB_PUSH_PLATFORM,
    endpoint_field,
)
from core.notifications.vapid import VAPID_SUBJECT_ENV, ensure_vapid_keys
from shared.streams import AUTH_SESSION_PREFIX, DEVICE_TOKENS_KEY
from tests.core.channels.conftest import (
    _TEST_SESSION_ID,
    live_channels_client,
    make_session_redis,
)

REGISTER = "/api/devices/register"
PUBLIC_KEY = "/api/push/public-key"
SUBJECT = "mailto:you@example.com"
APPLE = "https://web.push.apple.com/QGuQyavXutnMH8"
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _keys() -> dict[str, str]:
    """A browser's subscription keys: a real P-256 point and a 16-byte auth secret."""
    public = ec.generate_private_key(ec.SECP256R1()).public_key()
    point = public.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return {"p256dh": _b64url(point), "auth": _b64url(os.urandom(16))}


def _web_push(endpoint: str = APPLE, keys: dict[str, str] | None = None) -> dict[str, Any]:
    """The registration body the PWA sends: ``PushSubscription.toJSON()`` under a platform."""
    subscription = {"endpoint": endpoint, "expirationTime": None, "keys": keys or _keys()}
    return {"platform": WEB_PUSH_PLATFORM, "subscription": subscription}


def _row(endpoint: str, registered_at: str) -> str:
    return json.dumps(
        {
            "platform": WEB_PUSH_PLATFORM,
            "endpoint": endpoint,
            "keys": _keys(),
            "identity": "sir",
            "registered_at": registered_at,
            "user_agent": IPHONE,
        }
    )


def _endpoint(n: int) -> str:
    return f"{APPLE}-{n:02d}"


@pytest.fixture
def mock_redis():
    return make_session_redis()


@pytest.fixture
def app(mock_redis):
    with patch("core.channels.web_server.aioredis") as mock_aioredis:
        mock_aioredis.from_url.return_value = mock_redis
        app = create_app()
        app.state.redis = mock_redis
        yield app


@pytest.fixture
def client(app):
    """Signed-in client. Device registration takes a passkey session from anywhere."""
    c = TestClient(app)
    c.cookies.set("alfred_auth", _TEST_SESSION_ID)
    return c


@pytest.fixture
def anon_client(app):
    return TestClient(app)


def test_register_device_requires_session(anon_client, mock_redis) -> None:
    resp = anon_client.post(
        "/api/devices/register",
        json={
            "device_token": "aabbccdd11223344aabbccdd11223344",
            "platform": "ios",
            "identity": "sir",
        },
    )
    assert resp.status_code == 401
    mock_redis.hset.assert_not_called()


def test_unregister_device_requires_session(anon_client, mock_redis) -> None:
    resp = anon_client.request(
        "DELETE",
        "/api/devices/register",
        json={"device_token": "aabbccdd11223344aabbccdd11223344"},
    )
    assert resp.status_code == 401
    mock_redis.hdel.assert_not_called()


def test_register_device_stores_token(client, mock_redis) -> None:
    resp = client.post(
        "/api/devices/register",
        json={
            "device_token": "aabbccdd11223344aabbccdd11223344",
            "platform": "ios",
            "identity": "sir",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    mock_redis.hset.assert_called_once()
    call_args = mock_redis.hset.call_args
    assert call_args[0][0] == "alfred:push:devices"
    assert call_args[0][1] == "aabbccdd11223344aabbccdd11223344"
    stored = json.loads(call_args[0][2])
    assert stored["platform"] == "ios"
    assert stored["identity"] == "sir"
    assert "registered_at" in stored


def test_unregister_device_removes_token(client, mock_redis) -> None:
    resp = client.request(
        "DELETE",
        "/api/devices/register",
        json={"device_token": "aabbccdd11223344aabbccdd11223344"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    token = "aabbccdd11223344aabbccdd11223344"
    mock_redis.hdel.assert_called_once_with("alfred:push:devices", token)


def test_register_device_missing_token_returns_422(client) -> None:
    resp = client.post(
        "/api/devices/register",
        json={"platform": "ios", "identity": "sir"},
    )
    assert resp.status_code == 422


def test_register_device_short_token_rejected(client) -> None:
    """Device tokens shorter than 32 hex chars are rejected."""
    resp = client.post(
        "/api/devices/register",
        json={"device_token": "abc123", "platform": "ios", "identity": "sir"},
    )
    assert resp.status_code == 422


def test_register_device_non_hex_token_rejected(client) -> None:
    """Device tokens with non-hex characters are rejected."""
    resp = client.post(
        "/api/devices/register",
        json={
            "device_token": "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",
            "platform": "ios",
            "identity": "sir",
        },
    )
    assert resp.status_code == 422


def test_register_device_invalid_platform_rejected(client) -> None:
    """Only ios/ipados/macos platforms are accepted."""
    resp = client.post(
        "/api/devices/register",
        json={
            "device_token": "aabbccdd11223344aabbccdd11223344",
            "platform": "android",
            "identity": "sir",
        },
    )
    assert resp.status_code == 422


def test_unregister_device_invalid_token_rejected(client) -> None:
    """Unregistration also validates device token format."""
    resp = client.request(
        "DELETE",
        "/api/devices/register",
        json={"device_token": "short"},
    )
    assert resp.status_code == 422


# --- Web Push -----------------------------------------------------------------------


@pytest.fixture
def server() -> fakeredis.FakeServer:
    """One in-memory Redis, shared by the app (async) and the test (sync)."""
    shared = fakeredis.FakeServer()
    fakeredis.FakeRedis(server=shared).hset(
        f"{AUTH_SESSION_PREFIX}{_TEST_SESSION_ID}",
        mapping={"authenticated": "1", "credential_id": "test-cred"},
    )
    return shared


@pytest.fixture
def stored(server: fakeredis.FakeServer) -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(server=server, decode_responses=True)


@pytest.fixture
def push_app(server: fakeredis.FakeServer) -> Any:
    app = create_app(redis_url="redis://localhost:6379")
    app.state.redis = fakeredis.FakeAsyncRedis(server=server)
    return app


@pytest.fixture
def push_client(push_app: Any) -> TestClient:
    c = TestClient(push_app, headers={"User-Agent": IPHONE})
    c.cookies.set("alfred_auth", _TEST_SESSION_ID)
    return c


@pytest.fixture
def logs() -> Iterator[list[str]]:
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="DEBUG")
    yield messages
    logger.remove(sink_id)


@pytest.fixture
def registry() -> Iterator[None]:
    """Startup registers the adapter on the process-global registry; put it back after."""
    snapshot = dict(ChannelRegistry._instances)
    yield
    ChannelRegistry._instances.clear()
    ChannelRegistry._instances.update(snapshot)


@pytest.fixture
def data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """The keyring lock file lands under the data root: keep it out of the checkout."""
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))


def _stored_row(stored: fakeredis.FakeRedis, endpoint: str = APPLE) -> dict[str, Any] | None:
    raw = stored.hget(DEVICE_TOKENS_KEY, endpoint_field(endpoint))
    if raw is None:
        return None
    row: dict[str, Any] = json.loads(raw)
    return row


def _seed(stored: fakeredis.FakeRedis, numbers: Iterable[int]) -> None:
    """Store Web Push rows for ``_endpoint(n)``, registered on day ``n + 1`` of the month."""
    for n in numbers:
        stamp = f"2026-10-{n + 1:02d}T00:00:00+00:00"
        stored.hset(DEVICE_TOKENS_KEY, endpoint_field(_endpoint(n)), _row(_endpoint(n), stamp))


def test_registers_a_web_push_subscription(
    push_client: TestClient, stored: fakeredis.FakeRedis
) -> None:
    keys = _keys()
    resp = push_client.post(REGISTER, json=_web_push(keys=keys))

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    row = _stored_row(stored)
    assert row is not None
    registered_at = row.pop("registered_at")
    assert datetime.fromisoformat(registered_at).tzinfo is not None
    assert row == {
        "platform": WEB_PUSH_PLATFORM,
        "endpoint": APPLE,
        "keys": keys,
        "identity": "sir",
        "user_agent": IPHONE,
    }


def test_stores_the_keys_unpadded(push_client: TestClient, stored: fakeredis.FakeRedis) -> None:
    keys = _keys()
    padded = {name: value + "=" * (-len(value) % 4) for name, value in keys.items()}

    assert push_client.post(REGISTER, json=_web_push(keys=padded)).status_code == 200

    row = _stored_row(stored)
    assert row is not None
    assert row["keys"] == keys


def test_bounds_the_user_agent(push_client: TestClient, stored: fakeredis.FakeRedis) -> None:
    resp = push_client.post(REGISTER, json=_web_push(), headers={"User-Agent": "x" * 1000})

    assert resp.status_code == 200
    row = _stored_row(stored)
    assert row is not None
    assert row["user_agent"] == "x" * _MAX_USER_AGENT_LEN


def test_re_registering_replaces_the_row(
    push_client: TestClient, stored: fakeredis.FakeRedis
) -> None:
    second = _keys()
    assert push_client.post(REGISTER, json=_web_push()).status_code == 200
    assert push_client.post(REGISTER, json=_web_push(keys=second)).status_code == 200

    assert stored.hlen(DEVICE_TOKENS_KEY) == 1
    row = _stored_row(stored)
    assert row is not None
    assert row["keys"] == second


def test_refuses_an_endpoint_off_the_push_services(
    push_client: TestClient, stored: fakeredis.FakeRedis
) -> None:
    resp = push_client.post(REGISTER, json=_web_push("https://192.168.1.20/x"))

    assert resp.status_code == 422
    assert stored.hlen(DEVICE_TOKENS_KEY) == 0


def test_web_push_needs_a_session(push_app: Any, stored: fakeredis.FakeRedis) -> None:
    resp = TestClient(push_app).post(REGISTER, json=_web_push())

    assert resp.status_code == 401
    assert stored.hlen(DEVICE_TOKENS_KEY) == 0


def test_evicts_the_oldest_past_ten(push_client: TestClient, stored: fakeredis.FakeRedis) -> None:
    # Stored out of order, so the eviction cannot be riding the hash's insertion order.
    _seed(stored, reversed(range(MAX_WEB_PUSH_SUBSCRIPTIONS)))

    assert push_client.post(REGISTER, json=_web_push()).status_code == 200

    assert stored.hlen(DEVICE_TOKENS_KEY) == MAX_WEB_PUSH_SUBSCRIPTIONS
    assert _stored_row(stored, _endpoint(0)) is None  # the oldest
    assert _stored_row(stored, _endpoint(1)) is not None
    assert _stored_row(stored) is not None


def test_re_registering_at_the_cap_evicts_nothing(
    push_client: TestClient, stored: fakeredis.FakeRedis
) -> None:
    """The cap makes room for a new browser; one already counted needs none."""
    _seed(stored, range(MAX_WEB_PUSH_SUBSCRIPTIONS))

    assert push_client.post(REGISTER, json=_web_push(_endpoint(5))).status_code == 200

    assert stored.hlen(DEVICE_TOKENS_KEY) == MAX_WEB_PUSH_SUBSCRIPTIONS
    assert _stored_row(stored, _endpoint(0)) is not None


def test_apns_rows_do_not_count_toward_the_cap(
    push_client: TestClient, stored: fakeredis.FakeRedis
) -> None:
    apns = [f"{n:02d}" * 16 for n in range(5)]
    for token in apns:
        stored.hset(DEVICE_TOKENS_KEY, token, json.dumps({"platform": "ios", "identity": "sir"}))
    _seed(stored, range(MAX_WEB_PUSH_SUBSCRIPTIONS - 1))

    assert push_client.post(REGISTER, json=_web_push()).status_code == 200

    assert stored.hlen(DEVICE_TOKENS_KEY) == len(apns) + MAX_WEB_PUSH_SUBSCRIPTIONS
    assert all(stored.hexists(DEVICE_TOKENS_KEY, token) for token in apns)
    assert _stored_row(stored, _endpoint(0)) is not None


def test_unregisters_by_endpoint(push_client: TestClient, stored: fakeredis.FakeRedis) -> None:
    other = _endpoint(1)
    assert push_client.post(REGISTER, json=_web_push()).status_code == 200
    assert push_client.post(REGISTER, json=_web_push(other)).status_code == 200

    resp = push_client.request("DELETE", REGISTER, json={"endpoint": APPLE})

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert _stored_row(stored) is None
    assert _stored_row(stored, other) is not None


def test_unregistering_an_unknown_endpoint_is_ok(
    push_client: TestClient, stored: fakeredis.FakeRedis
) -> None:
    resp = push_client.request("DELETE", REGISTER, json={"endpoint": APPLE})

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.parametrize("body", [{}, {"device_token": "aa" * 16, "endpoint": APPLE}])
def test_unregister_needs_exactly_one(push_client: TestClient, body: dict[str, str]) -> None:
    assert push_client.request("DELETE", REGISTER, json=body).status_code == 422


def test_never_logs_the_subscription(push_client: TestClient, logs: list[str]) -> None:
    keys = _keys()
    assert push_client.post(REGISTER, json=_web_push(keys=keys)).status_code == 200
    assert push_client.request("DELETE", REGISTER, json={"endpoint": APPLE}).status_code == 200

    joined = "\n".join(logs)
    field = endpoint_field(APPLE)
    assert f"Registered web push subscription ({field[:12]}…, host=web.push.apple.com)" in joined
    assert field[12:] not in joined
    for secret in (APPLE, keys["p256dh"], keys["auth"]):
        assert secret not in joined


def test_public_key_unconfigured(push_client: TestClient) -> None:
    resp = push_client.get(PUBLIC_KEY)

    assert resp.status_code == 404
    assert resp.json() == {"detail": "Web Push not configured"}


def test_public_key_needs_no_session(push_app: Any) -> None:
    push_app.state.vapid_public_key = "B" * 87

    resp = TestClient(push_app).get(PUBLIC_KEY)

    assert resp.status_code == 200
    assert resp.json() == {"public_key": "B" * 87}


async def test_init_skips_without_a_subject(
    monkeypatch: pytest.MonkeyPatch, registry: None, data_dir: None, logs: list[str]
) -> None:
    monkeypatch.delenv(VAPID_SUBJECT_ENV, raising=False)
    ChannelRegistry._instances.pop("webpush", None)
    app = create_app()

    await _init_webpush_adapter(app, fakeredis.FakeAsyncRedis())

    assert ChannelRegistry.get_instance("webpush") is None
    assert app.state.vapid_public_key is None
    assert f"Web Push not configured ({VAPID_SUBJECT_ENV} unset), skipping adapter" in "\n".join(
        logs
    )


async def test_init_registers_with_a_subject(
    monkeypatch: pytest.MonkeyPatch, registry: None, data_dir: None
) -> None:
    monkeypatch.setenv(VAPID_SUBJECT_ENV, SUBJECT)
    app = create_app()

    await _init_webpush_adapter(app, fakeredis.FakeAsyncRedis())

    adapter = ChannelRegistry.get_instance("webpush")
    assert isinstance(adapter, WebPushChannelAdapter)
    # The key the browsers subscribe with is the one the keyring now holds.
    assert app.state.vapid_public_key == (await ensure_vapid_keys()).public_key
    await adapter.close()


def test_startup_registers_the_adapter_and_shutdown_closes_it(
    monkeypatch: pytest.MonkeyPatch, registry: None, data_dir: None
) -> None:
    monkeypatch.setenv(VAPID_SUBJECT_ENV, SUBJECT)
    close = AsyncMock()
    monkeypatch.setattr(WebPushChannelAdapter, "close", close)

    with live_channels_client(webpush=True) as live:
        assert isinstance(ChannelRegistry.get_instance("webpush"), WebPushChannelAdapter)
        resp = live.get(PUBLIC_KEY)
        assert resp.status_code == 200
        assert len(resp.json()["public_key"]) == 87
        close.assert_not_awaited()

    close.assert_awaited_once()


def test_a_failed_start_leaves_the_channel_up(
    monkeypatch: pytest.MonkeyPatch, registry: None, data_dir: None, logs: list[str]
) -> None:
    """An unreadable keyring costs Web Push alone, and never generates a key over it."""
    monkeypatch.setenv(VAPID_SUBJECT_ENV, SUBJECT)
    ChannelRegistry._instances.pop("webpush", None)
    monkeypatch.setattr(
        "core.notifications.vapid.aget_or_create_secret",
        AsyncMock(side_effect=RuntimeError("keyring locked")),
    )

    with live_channels_client(webpush=True) as live:
        assert live.get("/health").status_code == 200
        assert live.get(PUBLIC_KEY).status_code == 404

    assert ChannelRegistry.get_instance("webpush") is None
    assert any("Web Push adapter init failed" in line for line in logs)
