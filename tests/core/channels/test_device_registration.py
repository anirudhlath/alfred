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
from core.identity.auth_routes import MAX_USER_AGENT_LEN
from core.notifications import devices
from core.notifications.adapters.webpush import WebPushChannelAdapter
from core.notifications.channels import ChannelRegistry
from core.notifications.devices import (
    MAX_DEVICES_PER_FAMILY,
    STORE_ATTEMPTS,
    WEB_PUSH_PLATFORM,
    DeviceFamily,
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


def test_register_device_stores_token(push_client: TestClient, stored: fakeredis.FakeRedis) -> None:
    resp = push_client.post(
        "/api/devices/register",
        json={
            "device_token": "aabbccdd11223344aabbccdd11223344",
            "platform": "ios",
            "identity": "sir",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert stored.hkeys("alfred:push:devices") == ["aabbccdd11223344aabbccdd11223344"]
    row = json.loads(stored.hget("alfred:push:devices", "aabbccdd11223344aabbccdd11223344") or "")
    assert row["platform"] == "ios"
    assert row["identity"] == "sir"
    assert "registered_at" in row


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


def _stamp(n: int) -> str:
    return f"2026-10-{n + 1:02d}T00:00:00+00:00"


def _seed(stored: fakeredis.FakeRedis, numbers: Iterable[int]) -> None:
    """Store Web Push rows for ``_endpoint(n)``, registered on day ``n + 1`` of the month."""
    for n in numbers:
        stored.hset(DEVICE_TOKENS_KEY, endpoint_field(_endpoint(n)), _row(_endpoint(n), _stamp(n)))


def _token(n: int) -> str:
    return f"{n:02d}" * 16


def _apple(token: str = "aa" * 16) -> dict[str, str]:
    return {"device_token": token, "platform": "ios", "identity": "sir"}


def _seed_apple(stored: fakeredis.FakeRedis, numbers: Iterable[int]) -> None:
    """Store APNs rows for ``_token(n)``, registered on day ``n + 1`` of the month."""
    for n in numbers:
        row = {"platform": "ios", "identity": "sir", "registered_at": _stamp(n)}
        stored.hset(DEVICE_TOKENS_KEY, _token(n), json.dumps(row))


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
    assert row["user_agent"] == "x" * MAX_USER_AGENT_LEN


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
    _seed(stored, reversed(range(MAX_DEVICES_PER_FAMILY)))

    assert push_client.post(REGISTER, json=_web_push()).status_code == 200

    assert stored.hlen(DEVICE_TOKENS_KEY) == MAX_DEVICES_PER_FAMILY
    assert _stored_row(stored, _endpoint(0)) is None  # the oldest
    assert _stored_row(stored, _endpoint(1)) is not None
    assert _stored_row(stored) is not None


def test_re_registering_at_the_cap_evicts_nothing(
    push_client: TestClient, stored: fakeredis.FakeRedis
) -> None:
    """The cap makes room for a new browser; one already counted needs none."""
    _seed(stored, range(MAX_DEVICES_PER_FAMILY))

    assert push_client.post(REGISTER, json=_web_push(_endpoint(5))).status_code == 200

    assert stored.hlen(DEVICE_TOKENS_KEY) == MAX_DEVICES_PER_FAMILY
    assert _stored_row(stored, _endpoint(0)) is not None


def test_apns_rows_do_not_count_toward_the_cap(
    push_client: TestClient, stored: fakeredis.FakeRedis
) -> None:
    apns = [f"{n:02d}" * 16 for n in range(5)]
    for token in apns:
        stored.hset(DEVICE_TOKENS_KEY, token, json.dumps({"platform": "ios", "identity": "sir"}))
    _seed(stored, range(MAX_DEVICES_PER_FAMILY - 1))

    assert push_client.post(REGISTER, json=_web_push()).status_code == 200

    assert stored.hlen(DEVICE_TOKENS_KEY) == len(apns) + MAX_DEVICES_PER_FAMILY
    assert all(stored.hexists(DEVICE_TOKENS_KEY, token) for token in apns)
    assert _stored_row(stored, _endpoint(0)) is not None


def test_caps_apple_devices_too(push_client: TestClient, stored: fakeredis.FakeRedis) -> None:
    """Apple answers a made-up token 400, which prunes nothing, so the cap is the bound."""
    _seed_apple(stored, reversed(range(MAX_DEVICES_PER_FAMILY)))

    assert push_client.post(REGISTER, json=_apple()).status_code == 200

    assert stored.hlen(DEVICE_TOKENS_KEY) == MAX_DEVICES_PER_FAMILY
    assert not stored.hexists(DEVICE_TOKENS_KEY, _token(0))  # the oldest
    assert stored.hexists(DEVICE_TOKENS_KEY, _token(1))
    assert stored.hexists(DEVICE_TOKENS_KEY, "aa" * 16)


def test_re_registering_an_apple_device_at_the_cap_evicts_nothing(
    push_client: TestClient, stored: fakeredis.FakeRedis
) -> None:
    _seed_apple(stored, range(MAX_DEVICES_PER_FAMILY))

    assert push_client.post(REGISTER, json=_apple(_token(5))).status_code == 200

    assert stored.hlen(DEVICE_TOKENS_KEY) == MAX_DEVICES_PER_FAMILY
    assert stored.hexists(DEVICE_TOKENS_KEY, _token(0))
    row = json.loads(stored.hget(DEVICE_TOKENS_KEY, _token(5)) or "{}")
    assert row["registered_at"] != _stamp(5)  # replaced, not kept


def test_a_web_push_burst_never_evicts_an_iphone(
    push_client: TestClient, stored: fakeredis.FakeRedis
) -> None:
    _seed_apple(stored, [0])  # older than every browser

    for n in range(2 * MAX_DEVICES_PER_FAMILY):
        assert push_client.post(REGISTER, json=_web_push(_endpoint(n))).status_code == 200

    assert stored.hexists(DEVICE_TOKENS_KEY, _token(0))
    assert stored.hlen(DEVICE_TOKENS_KEY) == 1 + MAX_DEVICES_PER_FAMILY


def test_an_apple_burst_never_evicts_a_browser(
    push_client: TestClient, stored: fakeredis.FakeRedis
) -> None:
    _seed(stored, [0])  # older than every iPhone

    for n in range(2 * MAX_DEVICES_PER_FAMILY):
        assert push_client.post(REGISTER, json=_apple(_token(n + 1))).status_code == 200

    assert _stored_row(stored, _endpoint(0)) is not None
    assert stored.hlen(DEVICE_TOKENS_KEY) == 1 + MAX_DEVICES_PER_FAMILY


@pytest.mark.parametrize("corrupt", ["not json", "[1, 2]"])
@pytest.mark.parametrize("family", ["web_push", "apns"])
def test_a_corrupt_row_neither_counts_nor_goes(
    push_client: TestClient, stored: fakeredis.FakeRedis, corrupt: str, family: DeviceFamily
) -> None:
    seed, body = (_seed, _web_push()) if family == "web_push" else (_seed_apple, _apple())
    stored.hset(DEVICE_TOKENS_KEY, "corrupt", corrupt)
    seed(stored, range(MAX_DEVICES_PER_FAMILY - 1))

    assert push_client.post(REGISTER, json=body).status_code == 200  # nothing to evict
    assert stored.hlen(DEVICE_TOKENS_KEY) == 1 + MAX_DEVICES_PER_FAMILY

    seed(stored, [MAX_DEVICES_PER_FAMILY])  # one past the cap: the oldest goes, not the corrupt
    body = _web_push(_endpoint(99)) if family == "web_push" else _apple(_token(99))
    assert push_client.post(REGISTER, json=body).status_code == 200
    assert stored.hget(DEVICE_TOKENS_KEY, "corrupt") == corrupt
    assert stored.hlen(DEVICE_TOKENS_KEY) == 1 + MAX_DEVICES_PER_FAMILY


@pytest.mark.parametrize("family", ["web_push", "apns"])
def test_a_contended_store_answers_503(
    push_client: TestClient,
    stored: fakeredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
    logs: list[str],
    family: DeviceFamily,
) -> None:
    """Every attempt raced: the row is not written, and the client is told so."""
    real = devices.evictions
    racers = iter(range(STORE_ATTEMPTS + 1))

    def always_racing(
        rows: dict[bytes | str, bytes | str],
        field: str,
        family: DeviceFamily,
        cap: int = MAX_DEVICES_PER_FAMILY,
    ) -> list[str]:
        stored.hset(DEVICE_TOKENS_KEY, f"racer-{next(racers)}", "x")
        return real(rows, field, family, cap)

    monkeypatch.setattr(devices, "evictions", always_racing)
    body = _web_push() if family == "web_push" else _apple()

    resp = push_client.post(REGISTER, json=body)

    assert resp.status_code == 503
    assert resp.json() == {"detail": "Device store busy"}
    assert _stored_row(stored) is None
    assert not stored.hexists(DEVICE_TOKENS_KEY, "aa" * 16)
    assert any("Device registration gave up" in line for line in logs)


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


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"device_token": "aa" * 16, "endpoint": APPLE},
        {"endpoint": ""},
        {"device_token": "aa" * 16, "endpoint": ""},
    ],
)
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
    """An unreadable keyring costs Web Push alone: the channel stays up, no adapter registers."""
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
