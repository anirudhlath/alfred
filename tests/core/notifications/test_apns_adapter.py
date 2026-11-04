import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from core.notifications.adapters.apns import APNS_SANDBOX_URL, APNsChannelAdapter
from core.notifications.schema import Notification, Urgency


@pytest.fixture
def mock_redis() -> AsyncMock:
    r = AsyncMock()
    r.hgetall = AsyncMock(
        return_value={
            b"abc123": json.dumps(
                {"platform": "ios", "identity": "sir", "registered_at": "2026-04-03T00:00:00Z"}
            ).encode(),
        }
    )
    return r


@pytest.fixture
def notification() -> Notification:
    return Notification(
        title="Test Alert",
        body="Something happened",
        urgency=Urgency.IMPORTANT,
        source="test",
    )


def _make_adapter(
    redis: AsyncMock, *, status_code: int = 200, bundle_id: str = "com.test"
) -> tuple[APNsChannelAdapter, AsyncMock]:
    """Create an APNs adapter with a mocked httpx client."""
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_client.post = AsyncMock(return_value=mock_response)

    adapter = APNsChannelAdapter(
        redis=redis,
        team_id="T",
        key_id="K",
        private_key="pk",
        bundle_id=bundle_id,
    )
    adapter._client = mock_client
    return adapter, mock_client


@pytest.mark.asyncio
async def test_apns_adapter_sends_to_registered_devices(
    mock_redis: AsyncMock, notification: Notification
) -> None:
    adapter, mock_client = _make_adapter(mock_redis, bundle_id="com.alfred.app")

    with patch.object(adapter, "_get_auth_token", return_value="mock-jwt-token"):
        await adapter.deliver(notification)

    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert "abc123" in call_args[0][0]
    payload = json.loads(call_args[1]["content"])
    assert payload["aps"]["alert"]["title"] == "Test Alert"
    assert payload["aps"]["alert"]["body"] == "Something happened"
    assert payload["aps"]["sound"] == "default"
    assert payload["aps"]["interruption-level"] == "active"


@pytest.mark.asyncio
async def test_apns_adapter_informational_no_sound(mock_redis: AsyncMock) -> None:
    notif = Notification(title="FYI", body="Info", urgency=Urgency.INFORMATIONAL, source="test")
    adapter, mock_client = _make_adapter(mock_redis)

    with patch.object(adapter, "_get_auth_token", return_value="mock-jwt-token"):
        await adapter.deliver(notif)

    payload = json.loads(mock_client.post.call_args[1]["content"])
    assert payload["aps"].get("sound") is None
    assert payload["aps"]["interruption-level"] == "passive"


@pytest.mark.asyncio
async def test_apns_adapter_urgent_critical_alert(mock_redis: AsyncMock) -> None:
    notif = Notification(title="URGENT", body="Critical", urgency=Urgency.URGENT, source="test")
    adapter, mock_client = _make_adapter(mock_redis)

    with patch.object(adapter, "_get_auth_token", return_value="mock-jwt-token"):
        await adapter.deliver(notif)

    payload = json.loads(mock_client.post.call_args[1]["content"])
    assert payload["aps"]["sound"] == {"critical": 1, "name": "default", "volume": 1.0}
    assert payload["aps"]["interruption-level"] == "critical"


@pytest.mark.asyncio
async def test_apns_adapter_skips_when_no_devices(notification: Notification) -> None:
    empty_redis = AsyncMock()
    empty_redis.hgetall = AsyncMock(return_value={})

    adapter, mock_client = _make_adapter(empty_redis)
    await adapter.deliver(notification)
    mock_client.post.assert_not_called()


def test_apns_adapter_supports_all_urgencies() -> None:
    adapter = APNsChannelAdapter.__new__(APNsChannelAdapter)
    assert adapter.supports_urgency(Urgency.INFORMATIONAL)
    assert adapter.supports_urgency(Urgency.IMPORTANT)
    assert adapter.supports_urgency(Urgency.URGENT)


@pytest.mark.asyncio
async def test_apns_adapter_prunes_stale_token_on_410(mock_redis: AsyncMock) -> None:
    """APNs 410 response should trigger token removal from Redis."""
    notif = Notification(title="Test", body="Body", urgency=Urgency.IMPORTANT, source="test")
    adapter, _ = _make_adapter(mock_redis, status_code=410)

    with patch.object(adapter, "_get_auth_token", return_value="mock-jwt"):
        await adapter.deliver(notif)

    mock_redis.hdel.assert_called_once()


@pytest.mark.asyncio
async def test_skips_web_push_rows(mock_redis: AsyncMock, notification: Notification) -> None:
    """Web Push subscriptions share the device hash; they are the Web Push adapter's."""
    mock_redis.hgetall = AsyncMock(
        return_value={
            b"abc123": json.dumps({"platform": "ios", "identity": "sir"}).encode(),
            b"9f86d081884c": json.dumps(
                {
                    "platform": "web_push",
                    "endpoint": "https://web.push.apple.com/QGuQyavXutnMH8",
                    "keys": {"p256dh": "BPlaceholder", "auth": "placeholder"},
                }
            ).encode(),
        }
    )
    adapter, mock_client = _make_adapter(mock_redis)

    with patch.object(adapter, "_get_auth_token", return_value="mock-jwt"):
        await adapter.deliver(notification)

    mock_client.post.assert_called_once()
    assert mock_client.post.call_args[0][0].endswith("/3/device/abc123")


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", [["web_push"], ["ios"], {"a": 1}])
async def test_a_row_whose_platform_is_not_a_string_stops_nothing(
    mock_redis: AsyncMock, notification: Notification, platform: object
) -> None:
    """An outside writer's corrupt row does not stop delivery to the good token beside it."""
    good = await mock_redis.hgetall()
    mock_redis.hgetall = AsyncMock(
        return_value={**good, b"corrupt": json.dumps({"platform": platform}).encode()}
    )
    adapter, mock_client = _make_adapter(mock_redis)

    with patch.object(adapter, "_get_auth_token", return_value="mock-jwt"):
        await adapter.deliver(notification)

    urls = [call.args[0] for call in mock_client.post.call_args_list]
    assert any(url.endswith("/3/device/abc123") for url in urls)


@pytest.mark.asyncio
async def test_apns_adapter_sends_expiration_and_collapse_headers(
    mock_redis: AsyncMock, notification: Notification
) -> None:
    """Verify apns-expiration and apns-collapse-id headers are present."""
    adapter, mock_client = _make_adapter(mock_redis)

    with patch.object(adapter, "_get_auth_token", return_value="mock-jwt"):
        await adapter.deliver(notification)

    headers = mock_client.post.call_args[1]["headers"]
    assert "apns-expiration" in headers
    assert "apns-collapse-id" in headers
    assert headers["apns-collapse-id"] == notification.notification_id[:64]
    assert headers["apns-expiration"] != "0"


@pytest.mark.asyncio
async def test_apns_adapter_informational_immediate_expiration(mock_redis: AsyncMock) -> None:
    """Informational notifications should have apns-expiration=0 (immediate)."""
    notif = Notification(title="FYI", body="Info", urgency=Urgency.INFORMATIONAL, source="test")
    adapter, mock_client = _make_adapter(mock_redis)

    with patch.object(adapter, "_get_auth_token", return_value="mock-jwt"):
        await adapter.deliver(notif)

    headers = mock_client.post.call_args[1]["headers"]
    assert headers["apns-expiration"] == "0"


def test_apns_adapter_normalizes_private_key_newlines() -> None:
    """Escaped newlines in private key should be normalized."""
    adapter = APNsChannelAdapter(
        redis=AsyncMock(),
        team_id="T",
        key_id="K",
        private_key="-----BEGIN PRIVATE KEY-----\\nABC\\n-----END PRIVATE KEY-----",
        bundle_id="com.test",
    )
    assert "\\n" not in adapter._private_key
    assert "\n" in adapter._private_key


def test_apns_adapter_sandbox_from_env() -> None:
    """sandbox=None should read APNS_SANDBOX env var."""
    import os

    orig = os.environ.get("APNS_SANDBOX")
    try:
        os.environ["APNS_SANDBOX"] = "true"
        adapter = APNsChannelAdapter(
            redis=AsyncMock(),
            team_id="T",
            key_id="K",
            private_key="pk",
            bundle_id="com.test",
        )
        assert adapter._base_url == APNS_SANDBOX_URL
    finally:
        if orig is None:
            os.environ.pop("APNS_SANDBOX", None)
        else:
            os.environ["APNS_SANDBOX"] = orig


@pytest.mark.asyncio
async def test_apns_request_log_names_only_the_host(
    mock_redis: AsyncMock, notification: Notification, httpx_log_lines: list[str]
) -> None:
    """httpx logs each request's URL, and an APNs URL ends in the device token."""
    adapter = APNsChannelAdapter(
        redis=mock_redis, team_id="T", key_id="K", private_key="pk", bundle_id="com.test"
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as client:
        adapter._client = client
        with patch.object(adapter, "_get_auth_token", return_value="mock-jwt-token"):
            await adapter.deliver(notification)

    assert httpx_log_lines  # httpx logged the request
    assert all("abc123" not in line for line in httpx_log_lines)
    assert any("https://api.push.apple.com/…" in line for line in httpx_log_lines)
