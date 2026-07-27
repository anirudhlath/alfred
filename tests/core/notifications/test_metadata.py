"""Notification metadata flows from publisher through the WebSocket adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.notifications.schema import Notification, Urgency


def test_notification_metadata_defaults_empty() -> None:
    notif = Notification(title="T", body="B", urgency=Urgency.URGENT, source="test")
    assert notif.metadata == {}


def test_notification_metadata_json_roundtrip() -> None:
    notif = Notification(
        title="Confirmation required",
        body="Alfred wants to run 'home.unlock_door' — confirm?",
        urgency=Urgency.URGENT,
        source="domain-router",
        metadata={"pending_action_id": "req-1", "tool_name": "home.unlock_door"},
    )
    restored = Notification.model_validate_json(notif.model_dump_json())
    assert restored.metadata["pending_action_id"] == "req-1"


@pytest.mark.asyncio
async def test_publisher_passes_metadata_to_dispatcher() -> None:
    from core.notifications.publisher import NotificationPublisher

    dispatcher = AsyncMock()
    publisher = NotificationPublisher(dispatcher)
    await publisher.publish(
        title="T",
        body="B",
        source="domain-router",
        urgency=Urgency.URGENT,
        metadata={"pending_action_id": "req-2"},
    )
    dispatcher.dispatch.assert_awaited_once()
    notif = dispatcher.dispatch.call_args[0][0]
    assert notif.metadata == {"pending_action_id": "req-2"}


@pytest.mark.asyncio
async def test_websocket_payload_includes_metadata() -> None:
    from core.notifications.adapters.websocket import WebSocketChannelAdapter

    mock_ws = AsyncMock()
    adapter = WebSocketChannelAdapter(get_sessions=lambda: [mock_ws])
    notif = Notification(
        title="T",
        body="B",
        urgency=Urgency.IMPORTANT,
        source="domain-router",
        metadata={"pending_action_id": "req-3"},
    )
    await adapter.deliver(notif)
    payload = mock_ws.send_json.call_args[0][0]
    assert payload["metadata"] == {"pending_action_id": "req-3"}
