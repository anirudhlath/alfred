"""WebSocket channel filtering, and the follow-up sink that delivers late replies (#307)."""

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis
import pytest
from fastapi.testclient import TestClient

from bus.schemas.events import AlfredResponse, ChannelName
from core.channels.satellite.pipeline import satellite_session_id
from core.channels.web_server import (
    _active_websockets,
    _followup_sink,
    _session_sockets,
    get_active_websockets,
    get_web_websockets,
)
from core.notifications.dispatcher import NotificationDispatcher
from core.notifications.dnd import DNDChecker
from core.notifications.publisher import NotificationPublisher
from core.notifications.schema import Notification, Urgency
from shared.streams import NOTIFICATION_DISPATCH_STREAM


def test_get_web_websockets_excludes_ios() -> None:
    """iOS WebSocket connections should be excluded from web-only list."""
    ws_web = MagicMock()
    ws_ios = MagicMock()
    ws_voice = MagicMock()

    _active_websockets.clear()
    _active_websockets[ws_web] = "web_pwa"
    _active_websockets[ws_ios] = "ios"
    _active_websockets[ws_voice] = "voice"

    try:
        web_only = get_web_websockets()
        all_ws = get_active_websockets()

        assert len(web_only) == 2
        assert ws_web in web_only
        assert ws_voice in web_only
        assert ws_ios not in web_only

        assert len(all_ws) == 3
        assert ws_ios in all_ws
    finally:
        _active_websockets.clear()


def test_get_web_websockets_empty_when_only_ios() -> None:
    """If only iOS clients connected, web list should be empty."""
    ws_ios = MagicMock()

    _active_websockets.clear()
    _active_websockets[ws_ios] = "ios"

    try:
        assert get_web_websockets() == []
        assert len(get_active_websockets()) == 1
    finally:
        _active_websockets.clear()


# --- Follow-up delivery (#307) ---------------------------------------------------------


@pytest.fixture
def no_tts() -> Iterator[None]:
    with patch("core.channels.web_server.aget_tts", AsyncMock(return_value=None)):
        yield


@pytest.fixture
def session_sockets() -> Iterator[dict[str, set[Any]]]:
    _session_sockets.clear()
    try:
        yield _session_sockets
    finally:
        _session_sockets.clear()


def _notifier(redis: fakeredis.FakeAsyncRedis) -> NotificationPublisher:
    return NotificationPublisher(
        NotificationDispatcher(redis=redis, dnd_checker=DNDChecker(redis, calendar_adapter=None))
    )


async def _notifications(redis: fakeredis.FakeAsyncRedis) -> list[Notification]:
    entries = await redis.xrange(NOTIFICATION_DISPATCH_STREAM)
    return [Notification.model_validate_json(fields[b"notification"]) for _id, fields in entries]


def _followup(
    channel: ChannelName, session_id: str, text: str = "The door is locked, sir."
) -> AlfredResponse:
    return AlfredResponse(
        source="conscious-engine",
        channel=channel,
        session_id=session_id,
        text=text,
        actions_taken=["lock.lock"],
        mood="pleased",
        in_reply_to="req-1",
        followup=True,
    )


@pytest.mark.usefixtures("no_tts")
async def test_a_web_followup_reaches_the_sessions_socket(
    session_sockets: dict[str, set[Any]],
) -> None:
    redis = fakeredis.FakeAsyncRedis()
    mine, other = AsyncMock(), AsyncMock()
    session_sockets["s1"] = {mine}
    session_sockets["s2"] = {other}

    await _followup_sink(_notifier(redis), lambda: None)(_followup("web_pwa", "s1"))

    mine.send_json.assert_awaited_once_with(
        {
            "type": "response",
            "text": "The door is locked, sir.",
            "session_id": "s1",
            "actions_taken": ["lock.lock"],
            "mood": "pleased",
            "followup": True,
        }
    )
    other.send_json.assert_not_awaited()
    assert await _notifications(redis) == []


@pytest.mark.usefixtures("no_tts", "session_sockets")
async def test_a_web_followup_with_no_socket_goes_to_the_notifier() -> None:
    redis = fakeredis.FakeAsyncRedis()

    await _followup_sink(_notifier(redis), lambda: None)(_followup("voice", "s1"))

    [notification] = await _notifications(redis)
    assert notification.title == "Alfred"
    assert notification.body == "The door is locked, sir."
    assert notification.source == "channels"
    assert notification.urgency is Urgency.IMPORTANT


async def test_a_satellite_followup_is_spoken_on_its_satellite() -> None:
    bridge = AsyncMock()
    bridge.play_wav_to = AsyncMock(return_value=True)
    redis = fakeredis.FakeAsyncRedis()
    with (
        patch("core.channels.web_server.aget_tts", AsyncMock(return_value=object())),
        patch(
            "core.channels.web_server.synthesize_async", AsyncMock(return_value=b"RIFF-wav")
        ) as synthesize,
    ):
        await _followup_sink(_notifier(redis), lambda: bridge)(
            _followup("satellite", satellite_session_id("kitchen"))
        )

    assert synthesize.await_args.args[1] == "The door is locked, sir."
    bridge.play_wav_to.assert_awaited_once_with("kitchen", b"RIFF-wav")
    assert await _notifications(redis) == []


@pytest.mark.usefixtures("no_tts")
async def test_a_signal_followup_is_left_to_the_bridge(
    session_sockets: dict[str, set[Any]],
) -> None:
    redis = fakeredis.FakeAsyncRedis()
    socket, bridge = AsyncMock(), AsyncMock()
    session_sockets["signal-session"] = {socket}

    await _followup_sink(_notifier(redis), lambda: bridge)(_followup("signal", "signal-session"))

    socket.send_json.assert_not_awaited()
    bridge.play_wav_to.assert_not_awaited()
    assert await _notifications(redis) == []


@pytest.mark.usefixtures("no_tts")
def test_a_socket_is_on_its_session_while_open(
    web_client: TestClient, session_sockets: dict[str, set[Any]]
) -> None:
    """It joins under the session it restores, leaves the one it was given, and is gone
    once closed."""
    seen: list[dict[str, int]] = []

    async def publish(_redis: Any, request: Any, session_id: str, **_kw: Any) -> AlfredResponse:
        seen.append({sid: len(sockets) for sid, sockets in session_sockets.items()})
        return AlfredResponse(
            source="conscious-engine", channel="web_pwa", session_id=session_id, text="Yes, sir."
        )

    with (
        patch("core.channels.web_server.publish_and_wait", new=publish),
        web_client.websocket_connect("/ws") as ws,
    ):
        assert ws.receive_json()["type"] == "session"
        ws.send_json({"type": "text", "content": "Hello", "session_id": "restored"})
        frame = ws.receive_json()

    assert frame["followup"] is False
    assert seen == [{"restored": 1}]
    assert session_sockets == {}
