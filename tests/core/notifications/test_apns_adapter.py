import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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


@pytest.mark.asyncio
async def test_apns_adapter_sends_to_registered_devices(
    mock_redis: AsyncMock, notification: Notification
) -> None:
    from core.notifications.adapters.apns import APNsChannelAdapter

    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_client.post = AsyncMock(return_value=mock_response)

    adapter = APNsChannelAdapter(
        redis=mock_redis,
        team_id="TEAM123",
        key_id="KEY456",
        private_key="fake-key-content",
        bundle_id="com.alfred.app",
    )
    adapter._client = mock_client

    with patch.object(adapter, "_get_auth_token", return_value="mock-jwt-token"):
        await adapter.deliver(notification)

    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    # Verify APNs URL contains the device token
    assert "abc123" in call_args[0][0]
    # Verify payload structure
    payload = json.loads(call_args[1]["content"])
    assert payload["aps"]["alert"]["title"] == "Test Alert"
    assert payload["aps"]["alert"]["body"] == "Something happened"
    assert payload["aps"]["sound"] == "default"
    assert payload["aps"]["interruption-level"] == "active"


@pytest.mark.asyncio
async def test_apns_adapter_informational_no_sound(
    mock_redis: AsyncMock,
) -> None:
    from core.notifications.adapters.apns import APNsChannelAdapter

    notif = Notification(
        title="FYI",
        body="Info",
        urgency=Urgency.INFORMATIONAL,
        source="test",
    )

    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_client.post = AsyncMock(return_value=mock_response)

    adapter = APNsChannelAdapter(
        redis=mock_redis,
        team_id="T",
        key_id="K",
        private_key="pk",
        bundle_id="com.alfred.app",
    )
    adapter._client = mock_client

    with patch.object(adapter, "_get_auth_token", return_value="mock-jwt-token"):
        await adapter.deliver(notif)

    payload = json.loads(mock_client.post.call_args[1]["content"])
    assert payload["aps"].get("sound") is None
    assert payload["aps"]["interruption-level"] == "passive"


@pytest.mark.asyncio
async def test_apns_adapter_urgent_critical_alert(
    mock_redis: AsyncMock,
) -> None:
    from core.notifications.adapters.apns import APNsChannelAdapter

    notif = Notification(
        title="URGENT",
        body="Critical",
        urgency=Urgency.URGENT,
        source="test",
    )

    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_client.post = AsyncMock(return_value=mock_response)

    adapter = APNsChannelAdapter(
        redis=mock_redis,
        team_id="T",
        key_id="K",
        private_key="pk",
        bundle_id="com.alfred.app",
    )
    adapter._client = mock_client

    with patch.object(adapter, "_get_auth_token", return_value="mock-jwt-token"):
        await adapter.deliver(notif)

    payload = json.loads(mock_client.post.call_args[1]["content"])
    assert payload["aps"]["sound"] == {"critical": 1, "name": "default", "volume": 1.0}
    assert payload["aps"]["interruption-level"] == "critical"


@pytest.mark.asyncio
async def test_apns_adapter_skips_when_no_devices(notification: Notification) -> None:
    from core.notifications.adapters.apns import APNsChannelAdapter

    empty_redis = AsyncMock()
    empty_redis.hgetall = AsyncMock(return_value={})

    mock_client = AsyncMock()
    adapter = APNsChannelAdapter(
        redis=empty_redis,
        team_id="T",
        key_id="K",
        private_key="pk",
        bundle_id="com.alfred.app",
    )
    adapter._client = mock_client

    await adapter.deliver(notification)

    mock_client.post.assert_not_called()


def test_apns_adapter_supports_all_urgencies() -> None:
    from core.notifications.adapters.apns import APNsChannelAdapter

    adapter = APNsChannelAdapter.__new__(APNsChannelAdapter)
    assert adapter.supports_urgency(Urgency.INFORMATIONAL)
    assert adapter.supports_urgency(Urgency.IMPORTANT)
    assert adapter.supports_urgency(Urgency.URGENT)
