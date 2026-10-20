"""WebSocket channel filtering, and the follow-up sink that delivers late replies (#307)."""

import asyncio
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
    create_app,
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
    channel: ChannelName,
    session_id: str,
    text: str = "The door is locked, sir.",
    *,
    followup: bool = True,
) -> AlfredResponse:
    return AlfredResponse(
        source="conscious-engine",
        channel=channel,
        session_id=session_id,
        text=text,
        actions_taken=["lock.lock"],
        mood="pleased",
        in_reply_to="req-1",
        followup=followup,
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


@pytest.mark.usefixtures("no_tts")
async def test_a_late_answer_reaches_its_socket_as_a_followup(
    session_sockets: dict[str, set[Any]],
) -> None:
    """A late answer is no follow-up on the bus, but the socket stopped waiting for it: its
    frame says followup, or the Room takes it for the answer to the turn in flight."""
    redis = fakeredis.FakeAsyncRedis()
    socket = AsyncMock()
    session_sockets["s1"] = {socket}

    late = _followup("web_pwa", "s1", "Late, sir.", followup=False)
    await _followup_sink(_notifier(redis), lambda: None)(late)

    [frame] = [call.args[0] for call in socket.send_json.await_args_list]
    assert frame["text"] == "Late, sir."
    assert frame["followup"] is True


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
async def test_a_satellite_followup_with_no_bridge_goes_to_the_notifier() -> None:
    """A late answer to a spoken request is not lost because nothing can speak it."""
    redis = fakeredis.FakeAsyncRedis()

    await _followup_sink(_notifier(redis), lambda: None)(
        _followup("satellite", satellite_session_id("kitchen"))
    )

    [notification] = await _notifications(redis)
    assert notification.title == "Alfred"
    assert notification.body == "The door is locked, sir."
    assert notification.source == "channels"
    assert notification.urgency is Urgency.IMPORTANT


@pytest.mark.usefixtures("no_tts")
async def test_a_satellite_followup_with_no_tts_goes_to_the_notifier() -> None:
    bridge = AsyncMock()
    redis = fakeredis.FakeAsyncRedis()

    await _followup_sink(_notifier(redis), lambda: bridge)(
        _followup("satellite", satellite_session_id("kitchen"))
    )

    bridge.play_wav_to.assert_not_awaited()
    [notification] = await _notifications(redis)
    assert (notification.title, notification.source) == ("Alfred", "channels")
    assert notification.body == "The door is locked, sir."


async def test_a_satellite_followup_with_no_satellite_name_goes_to_the_notifier() -> None:
    bridge = AsyncMock()
    redis = fakeredis.FakeAsyncRedis()

    sink = _followup_sink(_notifier(redis), lambda: bridge)
    await sink(_followup("satellite", "not-a-satellite"))

    bridge.play_wav_to.assert_not_awaited()
    [notification] = await _notifications(redis)
    assert notification.body == "The door is locked, sir."


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


def test_lifespan_starts_the_followup_worker() -> None:
    """The lifespan starts followup_worker on the shared pool with the follow-up sink, its
    consumer name and the shutdown event it sets on the way out; the sink reads the satellite
    bridge per delivery. Patch set mirrors
    tests/core/channels/test_service_credentials.py::test_lifespan_starts_credential_push_worker.
    """
    calls: list[dict[str, Any]] = []
    sinks: list[tuple[Any, Any]] = []
    sink = AsyncMock()

    async def fake_worker(
        redis: Any, *, deliver: Any, consumer: str, shutdown: asyncio.Event
    ) -> None:
        calls.append(
            {"redis": redis, "deliver": deliver, "consumer": consumer, "shutdown": shutdown}
        )

    def fake_sink(notifier: Any, get_bridge: Any) -> Any:
        sinks.append((notifier, get_bridge))
        return sink

    mock_redis = AsyncMock()
    mock_redis.hgetall = AsyncMock(return_value={})
    mock_redis.close = AsyncMock()

    mock_store = AsyncMock()
    mock_store.get_user_id = AsyncMock(return_value=None)
    mock_store.list_credentials = AsyncMock(return_value=[])
    mock_store.has_any_credential = AsyncMock(return_value=False)

    def fake_warmup(service: str, steps: Any) -> Any:
        return asyncio.create_task(asyncio.sleep(0))

    with (
        patch("core.channels.web_server.aioredis.from_url", return_value=mock_redis),
        patch("core.channels.web_server.CredentialStore", return_value=mock_store),
        patch("core.channels.web_server._init_apns_adapter", new=AsyncMock()),
        patch("core.channels.web_server.start_warmup", new=fake_warmup),
        patch(
            "core.notifications.delivery.notification_delivery_worker",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "core.channels.service_credentials.credential_push_worker",
            new=AsyncMock(return_value=None),
        ),
        patch("core.channels.followups.followup_worker", new=fake_worker),
        patch("core.channels.web_server._followup_sink", new=fake_sink),
        patch("httpx.AsyncClient.aclose", new=AsyncMock()),
    ):
        app = create_app(redis_url="redis://localhost:6379")
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200

    [call] = calls
    assert call["redis"] is mock_redis
    assert call["deliver"] is sink
    assert call["consumer"] == "worker-1"
    assert call["shutdown"].is_set()
    [(notifier, get_bridge)] = sinks
    assert isinstance(notifier, NotificationPublisher)
    bridge = object()
    app.state.satellite_bridge = bridge
    assert get_bridge() is bridge
