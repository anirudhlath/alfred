from unittest.mock import AsyncMock

import pytest

from core.notifications.schema import Notification, Urgency


@pytest.mark.asyncio
async def test_websocket_payload_includes_notification_id() -> None:
    from core.notifications.adapters.websocket import WebSocketChannelAdapter

    mock_ws = AsyncMock()
    adapter = WebSocketChannelAdapter(get_sessions=lambda: [mock_ws])

    notif = Notification(
        title="Test",
        body="Body",
        urgency=Urgency.IMPORTANT,
        source="test",
    )

    await adapter.deliver(notif)

    mock_ws.send_json.assert_called_once()
    payload = mock_ws.send_json.call_args[0][0]
    assert payload["notification_id"] == notif.notification_id
