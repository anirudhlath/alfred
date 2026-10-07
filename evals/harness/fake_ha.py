"""A Home Assistant WebSocket API double for home-service, serving one World.

Speaks the subset home-service uses (alfred-home-service ``app/ha_connection.py``):
auth, subscribe_events, the three registry lists, get_states, get_services and
call_service. A served call_service is recorded, applied to the fake's state, and
pushed back as ``state_changed`` — so Alfred's live state follows, as with a real HA.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from evals.harness.evidence import HaCall, HaState

if TYPE_CHECKING:
    from websockets.asyncio.server import Server, ServerConnection

    from evals.harness.world import World

logger = logging.getLogger(__name__)

EVAL_HA_TOKEN = "alfred-eval-ha-token"
_HA_VERSION = "2026.7.0"

_SET_STATE: dict[tuple[str, str], str] = {
    ("light", "turn_on"): "on",
    ("light", "turn_off"): "off",
    ("switch", "turn_on"): "on",
    ("switch", "turn_off"): "off",
    ("media_player", "turn_on"): "on",
    ("media_player", "turn_off"): "off",
    ("media_player", "media_play"): "playing",
    ("media_player", "media_pause"): "paused",
    ("lock", "lock"): "locked",
    ("lock", "unlock"): "unlocked",
    ("cover", "open_cover"): "open",
    ("cover", "close_cover"): "closed",
    ("alarm_control_panel", "alarm_arm_away"): "armed_away",
    ("alarm_control_panel", "alarm_disarm"): "disarmed",
}

# The subscriptions a state change is pushed to: its own type, and HA's "every event".
_STATE_CHANGED_SUBSCRIPTIONS = ("state_changed", "*")


def _command_id(msg: Any) -> int | None:
    """The command's id, or None when HA would refuse the frame as incorrectly formatted:
    not an object, an id that is not a positive int, or no type."""
    if not isinstance(msg, dict):
        return None
    msg_id = msg.get("id")
    if type(msg_id) is not int or msg_id <= 0 or not msg.get("type"):
        return None
    return msg_id


def _check_fields(fields: dict[str, Any], data: dict[str, Any]) -> None:
    """Range-check *data* against the service's own number selectors, as HA's schema does.

    Raises ``ValueError`` for a value that is not a number, or one outside the
    selector's ``min``/``max`` (NaN included).
    """
    for key, spec in fields.items():
        number = ((spec or {}).get("selector") or {}).get("number")
        if key not in data or number is None:
            continue
        value = float(data[key])
        low, high = number.get("min"), number.get("max")
        if (low is not None and not value >= low) or (high is not None and not value <= high):
            raise ValueError(f"{key} must be between {low} and {high}, got {data[key]!r}")


def apply_service(
    states: dict[str, HaState],
    domain: str,
    service: str,
    data: dict[str, Any],
    entity_ids: list[str],
) -> dict[str, tuple[HaState, HaState]]:
    """Apply a service call to *states* in place; return {entity_id: (old, new)}.

    As in HA, a domain's service acts only on that domain's entities.
    """
    changed: dict[str, tuple[HaState, HaState]] = {}
    for entity_id in entity_ids:
        old = states.get(entity_id)
        if old is None or entity_id.split(".", 1)[0] != domain:
            continue
        attrs = dict(old.attributes)
        new_state = old.state
        if service == "toggle":
            new_state = "off" if old.state == "on" else "on"
        elif (domain, service) in _SET_STATE:
            new_state = _SET_STATE[(domain, service)]
        if domain == "light" and new_state == "on":
            if "brightness_pct" in data:
                attrs["brightness"] = round(float(data["brightness_pct"]) * 255 / 100)
            elif "brightness" in data:
                # HA's VALID_BRIGHTNESS: coerced to int, then clamped to 0..255.
                attrs["brightness"] = max(0, min(255, int(data["brightness"])))
        if domain == "light" and new_state == "off":
            attrs.pop("brightness", None)
        if domain == "media_player" and "volume_level" in data:
            attrs["volume_level"] = float(data["volume_level"])
        new = HaState(state=new_state, attributes=attrs)
        if new != old:
            states[entity_id] = new
            changed[entity_id] = (old, new)
    return changed


class FakeHA:
    """One fake Home Assistant serving *world*.

    ``calls`` holds every call_service HA would have run, in order. Each call's
    ``entity_ids`` are the targets it asked for, with areas expanded within the domain,
    not the entities it affected: an entity of another domain, or one without a state,
    is listed but left unchanged.
    """

    def __init__(
        self,
        world: World,
        *,
        token: str = EVAL_HA_TOKEN,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        self.world = world
        self.token = token
        self.host = host
        self._port = port
        self.calls: list[HaCall] = []
        self.connected = asyncio.Event()
        self._call_seen = asyncio.Event()  # replaced on every call; see ``record``
        self._states = world.initial_states()
        # Per connection: event type ("*" for every event) → the subscription ids, since HA
        # keeps every subscription, each pushed under its own id.
        self._subs: dict[ServerConnection, dict[str, list[int]]] = {}
        self._server: Server | None = None

    @property
    def port(self) -> int:
        return self._port

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self._port}"

    async def start(self) -> None:
        self._server = await serve(self._handler, self.host, self._port)
        self._port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    def reset(self) -> None:
        """Back to the world's initial state, for a new container."""
        self._states = self.world.initial_states()
        self.calls.clear()
        self.connected.clear()

    def states(self) -> dict[str, HaState]:
        return {k: v.model_copy(deep=True) for k, v in self._states.items()}

    def calls_between(self, t0: float, t1: float) -> list[HaCall]:
        return [c for c in self.calls if t0 <= c.t <= t1]

    def record(self, call: HaCall) -> None:
        """Log a call_service HA ran, and wake whoever waits for one."""
        self.calls.append(call)
        seen, self._call_seen = self._call_seen, asyncio.Event()
        seen.set()

    async def wait_for_call(self, since: float, timeout: float) -> bool:
        """Wait for a call_service made at or after *since*. False after *timeout* s."""
        try:
            async with asyncio.timeout(timeout):
                while not any(c.t >= since for c in self.calls):
                    await self._call_seen.wait()
        except TimeoutError:
            return False
        return True

    async def set_state(
        self, entity_id: str, state: str, attributes: dict[str, Any] | None = None
    ) -> None:
        old = self._states.get(entity_id)
        if old is None:
            raise KeyError(f"unknown entity {entity_id!r} in world {self.world.name!r}")
        new = HaState(state=state, attributes={**old.attributes, **(attributes or {})})
        self._states[entity_id] = new
        await self._push_change(entity_id, old, new)

    async def restore_world(self) -> int:
        """Push every entity that drifted from the world back to it. Returns the count."""
        restored = 0
        for entity_id, initial in self.world.initial_states().items():
            current = self._states.get(entity_id)
            if current != initial:
                self._states[entity_id] = initial
                if current is not None:
                    await self._push_change(entity_id, current, initial)
                restored += 1
        return restored

    def _targets(self, domain: str, target: dict[str, Any]) -> list[str]:
        ids: list[str] = []
        raw_ids = target.get("entity_id", [])
        ids += [raw_ids] if isinstance(raw_ids, str) else list(raw_ids)
        raw_areas = target.get("area_id", [])
        for area in [raw_areas] if isinstance(raw_areas, str) else list(raw_areas):
            ids += self.world.entity_ids_in_area(area, domain)
        return sorted(dict.fromkeys(ids))

    async def _handler(self, ws: ServerConnection) -> None:
        self._subs[ws] = {}
        try:
            await ws.send(json.dumps({"type": "auth_required", "ha_version": _HA_VERSION}))
            msg = json.loads(await ws.recv())
            if msg.get("type") != "auth" or msg.get("access_token") != self.token:
                await ws.send(
                    json.dumps({"type": "auth_invalid", "message": "Invalid access token"})
                )
                return
            await ws.send(json.dumps({"type": "auth_ok", "ha_version": _HA_VERSION}))
            async for raw in ws:
                msg = json.loads(raw)
                if (msg_id := _command_id(msg)) is None:
                    # As HA does: log it and answer invalid_format, under the frame's id
                    # when it is an object (whatever that id is), else 0.
                    logger.error("fake HA received an invalid command: %.200s", raw)
                    bad_id = msg.get("id") if isinstance(msg, dict) else 0
                    await self._error(
                        ws, bad_id, "invalid_format", "Message incorrectly formatted."
                    )
                    continue
                await self._command(ws, msg_id, msg)
        except ConnectionClosed:  # a dropped client is not the harness's failure
            logger.debug("fake HA connection closed", exc_info=True)
        except Exception:
            # A frame the fake could not handle (not JSON, as HA drops too, or a bug of
            # ours), so loud. The connection closes, as the handler has returned.
            logger.exception("fake HA dropped a connection on a frame it could not handle")
        finally:
            self._subs.pop(ws, None)

    async def _command(self, ws: ServerConnection, msg_id: int, msg: dict[str, Any]) -> None:
        match msg.get("type"):
            case "subscribe_events":
                self._subs[ws].setdefault(str(msg.get("event_type", "*")), []).append(msg_id)
                await self._result(ws, msg_id, None)
            case "config/entity_registry/list":
                await self._result(ws, msg_id, self.world.entity_registry())
            case "config/device_registry/list":
                await self._result(ws, msg_id, self.world.device_registry())
            case "config/area_registry/list":
                await self._result(ws, msg_id, self.world.area_registry())
            case "get_states":
                states = [
                    {"entity_id": k, "state": v.state, "attributes": v.attributes}
                    for k, v in self._states.items()
                ]
                await self._result(ws, msg_id, states)
            case "get_services":
                # Set before replying: once the reply is out, a waiter may already look.
                self.connected.set()
                await self._result(ws, msg_id, self.world.services)
            case "call_service":
                await self._call_service(ws, msg_id, msg)
            case other:
                await self._error(ws, msg_id, "unknown_command", f"Unknown command: {other}")

    async def _call_service(self, ws: ServerConnection, msg_id: int, msg: dict[str, Any]) -> None:
        t = time.monotonic()
        domain, service = str(msg.get("domain")), str(msg.get("service"))
        spec = self.world.services.get(domain, {}).get(service)
        if spec is None:
            await self._error(ws, msg_id, "not_found", f"Service {domain}.{service} not found.")
            return
        staged = dict(self._states)
        try:
            data = dict(msg.get("service_data") or {})
            _check_fields(spec.get("fields") or {}, data)
            # HA merges `target` into the service data and reads the targets from the
            # result, so an entity_id in service_data (home.call_service's free-form
            # data) targets too.
            entity_ids = self._targets(domain, {**data, **(msg.get("target") or {})})
            changed = apply_service(staged, domain, service, data, entity_ids)
            call = HaCall(
                t=t, domain=domain, service=service, service_data=data, entity_ids=entity_ids
            )
        except (TypeError, ValueError, ArithmeticError) as exc:
            # HA validates a call before running it, and answers a bad one with an error
            # (never by dropping the connection); nothing ran, so nothing is recorded.
            await self._error(ws, msg_id, "invalid_format", str(exc))
            return
        self.record(call)
        self._states = staged
        # Events first, then the result: what HA does for an entity that writes its state
        # during the call, so the caller's live state has moved by the time it returns.
        for entity_id, (old, new) in changed.items():
            await self._push_change(entity_id, old, new)
        await self._result(ws, msg_id, {"context": {"id": f"eval-{msg_id}"}, "response": None})

    async def _push_change(self, entity_id: str, old: HaState, new: HaState) -> None:
        def as_state(s: HaState) -> dict[str, Any]:
            return {"entity_id": entity_id, "state": s.state, "attributes": s.attributes}

        event = {
            "event_type": "state_changed",
            "data": {
                "entity_id": entity_id,
                "old_state": as_state(old),
                "new_state": as_state(new),
            },
        }
        for ws, subs in list(self._subs.items()):
            # One frame per matching subscription, each under its own id, as HA sends.
            frames = [
                json.dumps({"id": sub_id, "type": "event", "event": event})
                for key in _STATE_CHANGED_SUBSCRIPTIONS
                for sub_id in subs.get(key, [])
            ]
            try:
                for frame in frames:
                    await ws.send(frame)
            except ConnectionClosed:
                # Closed, but its handler has not unsubscribed it yet. One gone client
                # must not fail the push to the others, or the driver's set_state.
                logger.debug("fake HA: subscriber gone before %s's state_changed", entity_id)

    async def _result(self, ws: ServerConnection, msg_id: int, result: Any) -> None:
        await ws.send(
            json.dumps({"id": msg_id, "type": "result", "success": True, "result": result})
        )

    async def _error(self, ws: ServerConnection, msg_id: Any, code: str, message: str) -> None:
        await ws.send(
            json.dumps(
                {
                    "id": msg_id,
                    "type": "result",
                    "success": False,
                    "error": {"code": code, "message": message},
                }
            )
        )
