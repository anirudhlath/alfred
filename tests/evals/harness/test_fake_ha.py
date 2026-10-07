from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from logging import ERROR, WARNING
from typing import TYPE_CHECKING, Any, cast

import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, ConnectionClosedError

from evals.harness import fake_ha
from evals.harness.evidence import HaState
from evals.harness.fake_ha import EVAL_HA_TOKEN, FakeHA, apply_service
from evals.harness.world import load_world

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from websockets.asyncio.server import ServerConnection


async def recv(ws: ClientConnection) -> dict[str, Any]:
    """The next frame, failing the test rather than hanging when none comes."""
    return cast("dict[str, Any]", json.loads(await asyncio.wait_for(ws.recv(), timeout=5)))


async def handshake(
    ha: FakeHA, token: str = EVAL_HA_TOKEN
) -> tuple[ClientConnection, dict[str, Any]]:
    ws = await connect(ha.url.replace("http", "ws") + "/api/websocket")
    assert (await recv(ws))["type"] == "auth_required"
    await ws.send(json.dumps({"type": "auth", "access_token": token}))
    return ws, await recv(ws)


async def command(ws: ClientConnection, msg_id: int, **payload: Any) -> dict[str, Any]:
    await ws.send(json.dumps({"id": msg_id, **payload}))
    while True:
        msg = await recv(ws)
        if msg.get("type") == "result" and msg["id"] == msg_id:
            return msg


@pytest.fixture
async def ha() -> AsyncIterator[FakeHA]:
    server = FakeHA(load_world("apartment"))
    await server.start()
    yield server
    await server.stop()


async def test_rejects_a_bad_token(ha: FakeHA) -> None:
    ws, reply = await handshake(ha, token="wrong")
    assert reply["type"] == "auth_invalid"
    await ws.close()


async def test_serves_home_service_setup_and_marks_connected(ha: FakeHA) -> None:
    ws, reply = await handshake(ha)
    assert reply["type"] == "auth_ok"
    assert (await command(ws, 1, type="subscribe_events", event_type="state_changed"))["success"]
    states = (await command(ws, 2, type="get_states"))["result"]
    assert {s["entity_id"] for s in states} >= {"light.bedroom_lamp", "lock.front_door"}
    assert not ha.connected.is_set()
    services = (await command(ws, 3, type="get_services"))["result"]
    assert "turn_on" in services["light"]
    assert ha.connected.is_set()
    await ws.close()


async def test_serves_the_world_registries(ha: FakeHA) -> None:
    ws, _ = await handshake(ha)
    world = load_world("apartment")
    entities = await command(ws, 1, type="config/entity_registry/list")
    devices = await command(ws, 2, type="config/device_registry/list")
    areas = await command(ws, 3, type="config/area_registry/list")
    assert entities["result"] == world.entity_registry()
    assert devices["result"] == world.device_registry()
    assert areas["result"] == world.area_registry()
    unknown = await command(ws, 4, type="config/floor_registry/list")
    assert unknown["success"] is False and unknown["error"]["code"] == "unknown_command"
    await ws.close()


async def test_call_service_on_an_area_records_entities_and_pushes_state(ha: FakeHA) -> None:
    ws, _ = await handshake(ha)
    await command(ws, 1, type="subscribe_events", event_type="state_changed")
    call = {
        "id": 2,
        "type": "call_service",
        "domain": "light",
        "service": "turn_on",
        "service_data": {"brightness_pct": 50},
        "target": {"area_id": "bedroom"},
    }
    await ws.send(json.dumps(call))
    # As in HA for an entity that writes its state during the call: event, then result.
    event = await recv(ws)
    result = await recv(ws)
    assert event["type"] == "event" and event["id"] == 1
    assert event["event"]["data"]["new_state"]["state"] == "on"
    assert result["type"] == "result" and result["id"] == 2 and result["success"]
    assert ha.calls[-1].entity_ids == ["light.bedroom_lamp"]
    assert ha.states()["light.bedroom_lamp"] == HaState(
        state="on", attributes={"friendly_name": "Bedroom Lamp", "brightness": 128}
    )
    await ws.close()


async def test_wait_for_call_wakes_on_a_call_service_made_since(ha: FakeHA) -> None:
    ws, _ = await handshake(ha)
    since = time.monotonic()
    assert not await ha.wait_for_call(since, 0.01)  # nothing yet
    waiting = asyncio.create_task(ha.wait_for_call(since, 5))
    await asyncio.sleep(0.01)
    call = {"type": "call_service", "domain": "light", "service": "turn_on"}
    await command(ws, 1, **call, target={"entity_id": "light.bedroom_lamp"})
    assert await waiting
    # A call from before *since* does not count.
    assert not await ha.wait_for_call(time.monotonic(), 0.01)
    await ws.close()


async def test_a_subscription_to_every_event_gets_state_changes(ha: FakeHA) -> None:
    ws, _ = await handshake(ha)
    await command(ws, 1, type="subscribe_events")  # no event_type: every event, as in HA
    await ha.set_state("light.bedroom_lamp", "on")
    event = await recv(ws)
    assert event["id"] == 1 and event["event"]["event_type"] == "state_changed"
    assert event["event"]["data"]["entity_id"] == "light.bedroom_lamp"
    await ws.close()


async def test_an_entity_id_in_service_data_targets_as_in_ha(ha: FakeHA) -> None:
    # HA merges `target` into the service data, so home.call_service's free-form data
    # can carry the entity_id itself.
    ws, _ = await handshake(ha)
    result = await command(
        ws,
        1,
        type="call_service",
        domain="switch",
        service="turn_on",
        service_data={"entity_id": "switch.coffee_maker"},
    )
    assert result["success"]
    assert ha.calls[-1].entity_ids == ["switch.coffee_maker"]
    assert ha.states()["switch.coffee_maker"].state == "on"
    await ws.close()


async def test_unknown_service_is_an_error_and_not_recorded(ha: FakeHA) -> None:
    ws, _ = await handshake(ha)
    result = await command(
        ws,
        1,
        type="call_service",
        domain="fan",
        service="turn_on",
        target={"entity_id": "fan.bathroom"},
    )
    assert result["success"] is False and ha.calls == []
    assert result["error"]["code"] == "not_found"  # HA's ERR_NOT_FOUND
    await ws.close()


async def test_a_call_ha_would_reject_is_an_error_and_changes_nothing(ha: FakeHA) -> None:
    ws, _ = await handshake(ha)
    # Sorted, the pendants (on → off) come before the ceiling (off → on), whose
    # brightness is not a number: nothing may be half-applied. (`brightness` has no
    # selector in the world, so it gets past the field check and fails while applying.)
    result = await command(
        ws,
        1,
        type="call_service",
        domain="light",
        service="toggle",
        service_data={"brightness": "bright"},
        target={"entity_id": ["light.living_room_ceiling", "light.kitchen_pendants"]},
    )
    assert result["success"] is False and result["error"]["code"] == "invalid_format"
    assert ha.calls == []
    assert ha.states()["light.kitchen_pendants"].state == "on"
    # HA answers a bad call; it does not drop the connection.
    assert (await command(ws, 2, type="get_services"))["success"]
    await ws.close()


@pytest.mark.parametrize(
    ("domain", "service", "entity_id", "data"),
    [
        ("light", "turn_on", "light.bedroom_lamp", {"brightness_pct": 150}),
        ("light", "turn_on", "light.bedroom_lamp", {"brightness_pct": "inf"}),
        ("light", "turn_on", "light.bedroom_lamp", {"brightness_pct": "nan"}),
        ("media_player", "volume_set", "media_player.living_room_tv", {"volume_level": 5}),
    ],
)
async def test_a_number_outside_its_selector_range_is_rejected(
    ha: FakeHA, domain: str, service: str, entity_id: str, data: dict[str, Any]
) -> None:
    before = ha.states()[entity_id]
    ws, _ = await handshake(ha)
    result = await command(
        ws,
        1,
        type="call_service",
        domain=domain,
        service=service,
        service_data=data,
        target={"entity_id": [entity_id]},
    )
    assert result["success"] is False and result["error"]["code"] == "invalid_format"
    assert ha.calls == [] and ha.states()[entity_id] == before
    assert (await command(ws, 2, type="get_services"))["success"]
    await ws.close()


async def test_an_overflowing_number_is_an_error_not_a_dropped_connection(ha: FakeHA) -> None:
    ws, _ = await handshake(ha)
    # 1e999 is valid JSON that parses to inf; int(inf) raises OverflowError.
    await ws.send(
        '{"id": 1, "type": "call_service", "domain": "light", "service": "turn_on",'
        ' "service_data": {"brightness": 1e999}, "target": {"entity_id": ["light.bedroom_lamp"]}}'
    )
    result = await recv(ws)
    assert result["success"] is False and result["error"]["code"] == "invalid_format"
    assert ha.calls == [] and ha.states()["light.bedroom_lamp"].state == "off"
    assert (await command(ws, 2, type="get_services"))["success"]
    await ws.close()


async def test_a_client_closing_is_quiet_but_a_frame_that_is_not_json_is_logged(
    ha: FakeHA, caplog: pytest.LogCaptureFixture
) -> None:
    quiet = await connect(ha.url.replace("http", "ws") + "/api/websocket")
    await recv(quiet)  # auth_required
    await quiet.close()  # gone before authenticating
    bad, _ = await handshake(ha)
    await bad.send("{not json")  # HA drops a connection that sends this
    with pytest.raises(ConnectionClosed):
        await recv(bad)
    await ha.stop()  # waits for every handler to return
    errors = [r for r in caplog.records if r.name == fake_ha.__name__ and r.levelno >= WARNING]
    assert len(errors) == 1 and errors[0].levelno == ERROR and errors[0].exc_info


async def _raw_exchange(ha: FakeHA, data: bytes) -> None:
    """Send *data* over a bare TCP connection, half-close it, and wait until the server
    hangs up too."""
    reader, writer = await asyncio.open_connection(ha.host, ha.port)
    writer.write(data)
    writer.write_eof()
    with contextlib.suppress(ConnectionError):
        await asyncio.wait_for(reader.read(), timeout=5)
    writer.close()


def _handshake_failures(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == "opening handshake failed"]


async def test_a_bare_tcp_connect_like_the_probes_is_not_logged(
    ha: FakeHA, caplog: pytest.LogCaptureFixture
) -> None:
    await _raw_exchange(ha, b"")
    ws, reply = await handshake(ha)  # and the server still serves
    assert reply["type"] == "auth_ok"
    await ws.close()
    await ha.stop()  # waits for every connection, the probe's handshake included
    assert _handshake_failures(caplog) == []


async def test_a_real_handshake_error_is_still_logged(
    ha: FakeHA, caplog: pytest.LogCaptureFixture
) -> None:
    await _raw_exchange(ha, b"not an http request\r\n\r\n")
    await ha.stop()
    [failure] = _handshake_failures(caplog)
    assert failure.levelno == ERROR and failure.exc_info


@pytest.mark.parametrize(
    ("frame", "answered_id"),
    [
        ({"type": "get_states"}, None),  # no id
        ({"id": "7", "type": "get_states"}, "7"),  # not an int
        ({"id": True, "type": "get_states"}, True),
        ({"id": -1, "type": "get_states"}, -1),
        ({"id": 7}, 7),  # no type
        (["get_states"], 0),  # not an object
        (3, 0),
    ],
)
async def test_a_malformed_command_is_answered_invalid_format_as_ha_does(
    ha: FakeHA, caplog: pytest.LogCaptureFixture, frame: object, answered_id: object
) -> None:
    ws, _ = await handshake(ha)
    await ws.send(json.dumps(frame))
    answer = await recv(ws)
    assert answer["id"] == answered_id and answer["success"] is False
    assert answer["error"]["code"] == "invalid_format"
    # The connection stays up, and the bad frame is logged once.
    assert (await command(ws, 8, type="get_services"))["success"]
    errors = [r for r in caplog.records if r.name == fake_ha.__name__ and r.levelno >= WARNING]
    assert len(errors) == 1 and errors[0].levelno == ERROR
    await ws.close()


async def test_two_subscriptions_to_one_event_type_both_get_each_change(ha: FakeHA) -> None:
    ws, _ = await handshake(ha)
    await command(ws, 1, type="subscribe_events", event_type="state_changed")
    await command(ws, 2, type="subscribe_events", event_type="state_changed")
    await ha.set_state("light.bedroom_lamp", "on")
    ids = {(await recv(ws))["id"], (await recv(ws))["id"]}
    assert ids == {1, 2}  # HA keeps both, each under its own id
    await ws.close()


async def test_calls_between_and_reset(ha: FakeHA) -> None:
    ws, _ = await handshake(ha)
    await command(ws, 1, type="get_services")
    t0 = time.monotonic()
    await command(
        ws,
        2,
        type="call_service",
        domain="lock",
        service="unlock",
        target={"entity_id": ["lock.front_door"]},
    )
    t1 = time.monotonic()
    assert [c.service for c in ha.calls_between(t0, t1)] == ["unlock"]
    assert ha.calls_between(0.0, t0) == []
    assert ha.states()["lock.front_door"].state == "unlocked"
    ha.reset()
    assert ha.calls == [] and not ha.connected.is_set()
    assert ha.states()["lock.front_door"].state == "locked"
    await ws.close()


async def test_set_state_and_restore_world_push_to_subscribers(ha: FakeHA) -> None:
    ws, _ = await handshake(ha)
    await command(ws, 1, type="subscribe_events", event_type="state_changed")
    await ha.set_state("light.living_room_ceiling", "on", {"brightness": 200})
    assert ha.states()["light.living_room_ceiling"].state == "on"
    pushed = (await recv(ws))["event"]["data"]
    assert pushed["new_state"]["attributes"]["brightness"] == 200
    assert await ha.restore_world() == 1
    assert ha.states()["light.living_room_ceiling"].state == "off"
    restored = (await recv(ws))["event"]["data"]
    assert restored["entity_id"] == "light.living_room_ceiling"
    assert restored["old_state"]["state"] == "on" and restored["new_state"]["state"] == "off"
    assert "brightness" not in restored["new_state"]["attributes"]
    with pytest.raises(KeyError):
        await ha.set_state("light.nope", "on")
    await ws.close()


class _GoneSocket:
    """A subscriber whose connection closed before its handler unsubscribed it."""

    async def send(self, message: str) -> None:
        raise ConnectionClosedError(None, None)


def _gone_subscriber_first(ha: FakeHA) -> None:
    # First in line, so a push that stopped at it would never reach the live one.
    ha._subs = {cast("ServerConnection", _GoneSocket()): {"state_changed": [7]}, **ha._subs}


async def test_a_subscriber_that_went_away_does_not_stop_the_push(ha: FakeHA) -> None:
    ws, _ = await handshake(ha)
    await command(ws, 1, type="subscribe_events", event_type="state_changed")
    _gone_subscriber_first(ha)
    await ha.set_state("light.bedroom_lamp", "on")
    event = await recv(ws)
    assert event["event"]["data"]["entity_id"] == "light.bedroom_lamp"
    await ws.close()


async def test_a_subscriber_that_went_away_does_not_kill_the_callers_connection(
    ha: FakeHA,
) -> None:
    ws, _ = await handshake(ha)
    await command(ws, 1, type="subscribe_events", event_type="state_changed")
    _gone_subscriber_first(ha)
    call = {
        "id": 2,
        "type": "call_service",
        "domain": "light",
        "service": "turn_on",
        "target": {"entity_id": ["light.bedroom_lamp"]},
    }
    await ws.send(json.dumps(call))
    event = await recv(ws)
    result = await recv(ws)
    assert event["event"]["data"]["entity_id"] == "light.bedroom_lamp"
    assert result["id"] == 2 and result["success"]
    assert (await command(ws, 3, type="get_services"))["success"]
    await ws.close()


def test_apply_service_effects() -> None:
    states = {
        "light.a": HaState(state="on", attributes={"brightness": 255}),
        "switch.b": HaState(state="off"),
        "media_player.c": HaState(state="off"),
    }
    changed = apply_service(states, "light", "turn_off", {}, ["light.a"])
    assert changed["light.a"][1] == HaState(state="off")
    apply_service(states, "switch", "toggle", {}, ["switch.b"])
    assert states["switch.b"].state == "on"
    apply_service(states, "media_player", "volume_set", {"volume_level": 0.2}, ["media_player.c"])
    assert states["media_player.c"].attributes["volume_level"] == 0.2


@pytest.mark.parametrize(("given", "kept"), [(300, 255), (-5, 0), (128.6, 128), ("77", 77)])
def test_apply_service_clamps_brightness_as_ha_does(given: object, kept: int) -> None:
    # HA's VALID_BRIGHTNESS coerces to int, then clamps to 0..255.
    states = {"light.a": HaState(state="off")}
    apply_service(states, "light", "turn_on", {"brightness": given}, ["light.a"])
    assert states["light.a"].attributes["brightness"] == kept


def test_apply_service_leaves_other_domains_alone() -> None:
    # A domain's service acts only on that domain's entities, as in HA.
    states = {"switch.b": HaState(state="off")}
    assert apply_service(states, "light", "turn_on", {}, ["switch.b"]) == {}
    assert states["switch.b"].state == "off"
