from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any, cast

import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosedError

from evals.harness.evidence import HaState
from evals.harness.fake_ha import EVAL_HA_TOKEN, FakeHA, apply_service
from evals.harness.world import load_world

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from websockets.asyncio.server import ServerConnection


async def handshake(
    ha: FakeHA, token: str = EVAL_HA_TOKEN
) -> tuple[ClientConnection, dict[str, Any]]:
    ws = await connect(ha.url.replace("http", "ws") + "/api/websocket")
    assert json.loads(await ws.recv())["type"] == "auth_required"
    await ws.send(json.dumps({"type": "auth", "access_token": token}))
    return ws, json.loads(await ws.recv())


async def command(ws: ClientConnection, msg_id: int, **payload: Any) -> dict[str, Any]:
    await ws.send(json.dumps({"id": msg_id, **payload}))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("type") == "result" and msg["id"] == msg_id:
            return cast("dict[str, Any]", msg)


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
    result = await command(
        ws,
        2,
        type="call_service",
        domain="light",
        service="turn_on",
        service_data={"brightness_pct": 50},
        target={"area_id": "bedroom"},
    )
    assert result["success"]
    assert ha.calls[-1].entity_ids == ["light.bedroom_lamp"]
    assert ha.states()["light.bedroom_lamp"] == HaState(
        state="on", attributes={"friendly_name": "Bedroom Lamp", "brightness": 128}
    )
    event = json.loads(await ws.recv())
    assert event["id"] == 1 and event["event"]["data"]["new_state"]["state"] == "on"
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
    # brightness is not a number: nothing may be half-applied.
    result = await command(
        ws,
        1,
        type="call_service",
        domain="light",
        service="toggle",
        service_data={"brightness_pct": "bright"},
        target={"entity_id": ["light.living_room_ceiling", "light.kitchen_pendants"]},
    )
    assert result["success"] is False and result["error"]["code"] == "invalid_format"
    assert ha.calls == []
    assert ha.states()["light.kitchen_pendants"].state == "on"
    # HA answers a bad call; it does not drop the connection.
    assert (await command(ws, 2, type="get_services"))["success"]
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


async def test_set_state_and_restore_world() -> None:
    ha = FakeHA(load_world("apartment"))
    await ha.set_state("light.living_room_ceiling", "on", {"brightness": 200})
    assert ha.states()["light.living_room_ceiling"].state == "on"
    assert await ha.restore_world() == 1
    assert ha.states()["light.living_room_ceiling"].state == "off"
    with pytest.raises(KeyError):
        await ha.set_state("light.nope", "on")


class _GoneSocket:
    """A subscriber whose connection closed before its handler unsubscribed it."""

    async def send(self, message: str) -> None:
        raise ConnectionClosedError(None, None)


async def test_a_subscriber_that_went_away_does_not_stop_the_push(ha: FakeHA) -> None:
    ws, _ = await handshake(ha)
    await command(ws, 1, type="subscribe_events", event_type="state_changed")
    ha._subs[cast("ServerConnection", _GoneSocket())] = {"state_changed": 7}
    await ha.set_state("light.bedroom_lamp", "on")
    event = json.loads(await ws.recv())
    assert event["event"]["data"]["entity_id"] == "light.bedroom_lamp"
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


def test_apply_service_leaves_other_domains_alone() -> None:
    # A domain's service acts only on that domain's entities, as in HA.
    states = {"switch.b": HaState(state="off")}
    assert apply_service(states, "light", "turn_on", {}, ["switch.b"]) == {}
    assert states["switch.b"].state == "off"
