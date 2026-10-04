"""Availability bridging — `unavailable`, `unknown` and no state carry no information.

A device dropping off the network and coming back is not something that happened
at home. The bridge compares the state an entity comes back in with the last real
state it had: the same state is a blip and is dropped, a different one is a single
real change. It runs before the attention gate, so a blip costs no inference and
starts no cooldown.

What the bridge remembers is checked through what it does next, not by reading
what it stored, so the tests hold whatever the stored shape is. The two exceptions
plant corrupt values on purpose.
"""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from bus.schemas.events import ReflexObservation, StateChangedEvent
from shared.streams import AVAILABILITY_DECISION_PREFIX, LAST_KNOWN_STATE_KEY
from tests.helpers import attention_redis

if TYPE_CHECKING:
    from core.reflex.attention import AttentionSet

ENTITY = "media_player.shield"

# Stream entry IDs in arrival order, as Redis assigns them. A test takes one per
# delivery, and passes the same one again to replay it.
_ENTRY_MS = itertools.count(1_759_600_000_000)


def _next_entry() -> bytes:
    return f"{next(_ENTRY_MS)}-0".encode()


def _event(old: str | None, new: str, entity_id: str = ENTITY) -> StateChangedEvent:
    return StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id=entity_id,
        old_state=old,
        new_state=new,
        attributes={"friendly_name": "Shield"},
    )


def _with_hash(redis: AsyncMock) -> AsyncMock:
    """Back HGET/HSET and GET/SET with dicts, handing back bytes as the production pool does."""
    hashes: dict[str, dict[str, str]] = {}
    strings: dict[str, str] = {}

    async def _hget(key: str, field: str) -> bytes | None:
        value = hashes.get(key, {}).get(field)
        return None if value is None else value.encode()

    async def _hset(key: str, field: str, value: str) -> int:
        hashes.setdefault(key, {})[field] = value
        return 1

    async def _get(key: str) -> bytes | None:
        value = strings.get(key)
        return None if value is None else value.encode()

    async def _set(key: str, value: str, **_kwargs: object) -> bool:
        strings[key] = value
        return True

    redis.hashes = hashes
    redis.strings = strings
    redis.hget = AsyncMock(side_effect=_hget)
    redis.hset = AsyncMock(side_effect=_hset)
    redis.get = AsyncMock(side_effect=_get)
    redis.set = AsyncMock(side_effect=_set)
    return redis


async def _bridge(
    redis: AsyncMock, event: StateChangedEvent, entry: bytes | None = None
) -> StateChangedEvent | None:
    from core.reflex.availability import bridge_availability

    return await bridge_availability(redis, event, entry if entry is not None else _next_entry())


async def _seeded(state: str, redis: AsyncMock | None = None) -> AsyncMock:
    """A hash that has seen ENTITY settle in ``state`` through a real change."""
    redis = _with_hash(redis if redis is not None else AsyncMock())
    assert await _bridge(redis, _event("on" if state == "off" else "off", state)) is not None
    return redis


def _transition(event: StateChangedEvent | None) -> tuple[str | None, str] | None:
    return None if event is None else (event.old_state, event.new_state)


# ---------------------------------------------------------------------------
# The bridge itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_going_unavailable_is_dropped_and_forgets_nothing() -> None:
    redis = await _seeded("off")

    assert await _bridge(redis, _event("off", "unavailable")) is None
    assert _transition(await _bridge(redis, _event("unavailable", "on"))) == ("off", "on")


@pytest.mark.asyncio
async def test_coming_back_in_the_same_state_is_a_blip() -> None:
    redis = await _seeded("off")

    assert await _bridge(redis, _event("unavailable", "off")) is None


@pytest.mark.asyncio
async def test_coming_back_in_a_different_state_is_one_change() -> None:
    """off → unavailable → on happened: the device was turned on while unreachable."""
    redis = await _seeded("off")
    event = _event("unavailable", "on")

    bridged = await _bridge(redis, event)

    assert _transition(bridged) == ("off", "on")
    # The rest of the event — and above all its timestamp — is the event's own.
    assert bridged is not None
    assert bridged.model_dump(exclude={"old_state"}) == event.model_dump(exclude={"old_state"})
    # And "on" is now the state to compare the next return against.
    assert await _bridge(redis, _event("unavailable", "on")) is None


@pytest.mark.asyncio
async def test_coming_back_with_no_known_state_is_dropped_but_remembered() -> None:
    """Nothing to compare against (first sight, or the hash is new) — no change to claim."""
    redis = _with_hash(AsyncMock())

    assert await _bridge(redis, _event("unavailable", "on")) is None
    assert _transition(await _bridge(redis, _event("unavailable", "off"))) == ("on", "off")


@pytest.mark.asyncio
@pytest.mark.parametrize(("old", "new"), [("on", "unknown"), ("unknown", "on")])
async def test_unknown_is_treated_like_unavailable(old: str, new: str) -> None:
    redis = await _seeded("on")

    assert await _bridge(redis, _event(old, new)) is None


@pytest.mark.asyncio
async def test_no_old_state_is_treated_like_unavailable() -> None:
    """HA re-adds every entity after a restart as ``None → state``: a return, not a change."""
    redis = await _seeded("off")

    assert await _bridge(redis, _event(None, "off")) is None
    assert _transition(await _bridge(redis, _event(None, "on"))) == ("off", "on")


@pytest.mark.asyncio
async def test_no_old_state_with_nothing_known_is_dropped_but_remembered() -> None:
    redis = _with_hash(AsyncMock())

    assert await _bridge(redis, _event(None, "on")) is None
    assert _transition(await _bridge(redis, _event("unavailable", "off"))) == ("on", "off")


@pytest.mark.asyncio
async def test_unknown_to_unavailable_is_dropped_and_forgets_nothing() -> None:
    redis = await _seeded("on")

    assert await _bridge(redis, _event("unknown", "unavailable")) is None
    assert await _bridge(redis, _event("unavailable", "on")) is None


@pytest.mark.asyncio
async def test_a_real_change_passes_unchanged_and_is_remembered() -> None:
    redis = _with_hash(AsyncMock())
    event = _event("off", "on")

    assert await _bridge(redis, event) is event
    assert _transition(await _bridge(redis, _event("unavailable", "off"))) == ("on", "off")


@pytest.mark.asyncio
async def test_an_empty_string_is_a_state_like_any_other() -> None:
    """An ``input_text`` can be ``""``; leaving it is a change, on every delivery."""
    redis = await _seeded("")
    returned, entry = _event("unavailable", "hello"), _next_entry()

    assert _transition(await _bridge(redis, returned, entry)) == ("", "hello")
    assert _transition(await _bridge(redis, returned, entry)) == ("", "hello")


@pytest.mark.asyncio
async def test_an_unreadable_stored_state_counts_as_unknown() -> None:
    redis = _with_hash(AsyncMock())
    redis.hashes[LAST_KNOWN_STATE_KEY] = {ENTITY: "not json {"}

    assert await _bridge(redis, _event("unavailable", "on")) is None


@pytest.mark.asyncio
async def test_a_failed_write_still_passes_a_real_change() -> None:
    """Remembering is bookkeeping. Under maxmemory HSET is refused while XACK is not."""
    redis = _with_hash(AsyncMock())
    redis.hset = AsyncMock(side_effect=RuntimeError("OOM command not allowed"))
    event = _event("off", "on")

    assert await _bridge(redis, event) is event


@pytest.mark.asyncio
async def test_a_failed_write_still_bridges_a_return_from_unavailable() -> None:
    redis = await _seeded("off")
    redis.hset = AsyncMock(side_effect=RuntimeError("OOM command not allowed"))

    assert _transition(await _bridge(redis, _event("unavailable", "on"))) == ("off", "on")


@pytest.mark.asyncio
async def test_a_failed_lookup_drops_a_return_from_unavailable() -> None:
    """Without the last known state there is no change to claim."""
    redis = await _seeded("off")
    redis.hget = AsyncMock(side_effect=RuntimeError("connection reset"))

    assert await _bridge(redis, _event("unavailable", "on")) is None


# ---------------------------------------------------------------------------
# Redelivery
#
# The runner ACKs only on success, and its reclaim pass replays what failed (the
# model down, a tool call raising) under the same stream entry ID. By then the
# bridge has recorded the event, and newer events for the entity have often moved
# its state on, so a replay must not be decided against either.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_redelivered_return_is_bridged_again() -> None:
    redis = await _seeded("off")
    returned, entry = _event("unavailable", "on"), _next_entry()

    assert _transition(await _bridge(redis, returned, entry)) == ("off", "on")
    assert _transition(await _bridge(redis, returned, entry)) == ("off", "on")


@pytest.mark.asyncio
async def test_a_redelivered_blip_stays_a_blip() -> None:
    redis = await _seeded("off")
    blip, entry = _event("unavailable", "off"), _next_entry()

    assert await _bridge(redis, blip, entry) is None
    assert await _bridge(redis, blip, entry) is None


@pytest.mark.asyncio
async def test_a_return_replayed_after_a_newer_event_is_bridged_again() -> None:
    """Media players send attribute-only updates every few seconds, so something
    usually lands between a failed delivery and its replay."""
    redis = await _seeded("off")
    returned, entry = _event("unavailable", "on"), _next_entry()

    assert _transition(await _bridge(redis, returned, entry)) == ("off", "on")
    # A new title on the same entity, while the return waits for the reclaim pass.
    assert await _bridge(redis, _event("on", "on")) is not None
    assert _transition(await _bridge(redis, returned, entry)) == ("off", "on")


@pytest.mark.asyncio
async def test_a_blip_replayed_after_a_newer_event_stays_a_blip() -> None:
    redis = await _seeded("off")
    blip, entry = _event("unavailable", "off"), _next_entry()

    assert await _bridge(redis, blip, entry) is None
    assert await _bridge(redis, _event("off", "on")) is not None
    assert await _bridge(redis, blip, entry) is None


@pytest.mark.asyncio
async def test_a_late_replay_does_not_roll_the_known_state_back() -> None:
    """off → on failed downstream, on → off went through, then off → on is replayed.

    The light is off. Were the replay to record "on", the next blip back as "off"
    would be stored and sent to the model as a turn-off that never happened.
    """
    redis = await _seeded("off")
    turned_on, on_entry = _event("off", "on"), _next_entry()
    turned_off = _event("on", "off")

    assert await _bridge(redis, turned_on, on_entry) is turned_on
    assert await _bridge(redis, turned_off) is turned_off
    assert await _bridge(redis, turned_on, on_entry) is turned_on  # the replay still goes on

    assert await _bridge(redis, _event("unavailable", "off")) is None


@pytest.mark.asyncio
async def test_entries_in_the_same_millisecond_keep_their_order() -> None:
    """``ms-seq`` sorts by both parts as numbers: as strings, ``-10`` would sort before ``-9``."""
    redis = await _seeded("off")
    ms = next(_ENTRY_MS)
    turned_on, on_entry = _event("off", "on"), f"{ms}-9".encode()

    assert await _bridge(redis, turned_on, on_entry) is turned_on
    assert await _bridge(redis, _event("on", "off"), f"{ms}-10".encode()) is not None
    assert await _bridge(redis, turned_on, on_entry) is turned_on  # a late replay

    assert await _bridge(redis, _event("unavailable", "off")) is None


@pytest.mark.asyncio
async def test_a_replay_retries_a_write_the_first_delivery_lost() -> None:
    """Otherwise the next return is compared against the state before this one."""
    redis = await _seeded("off")
    save = redis.hset.side_effect
    returned, entry = _event("unavailable", "on"), _next_entry()

    redis.hset.side_effect = RuntimeError("OOM command not allowed")
    assert _transition(await _bridge(redis, returned, entry)) == ("off", "on")
    redis.hset.side_effect = save
    assert _transition(await _bridge(redis, returned, entry)) == ("off", "on")

    assert await _bridge(redis, _event("unavailable", "on")) is None


@pytest.mark.asyncio
async def test_only_the_same_delivery_is_treated_as_a_replay() -> None:
    """A fresh return in the state the last one left is a blip, not a second change."""
    redis = await _seeded("off")

    assert _transition(await _bridge(redis, _event("unavailable", "on"))) == ("off", "on")
    assert await _bridge(redis, _event("on", "unavailable")) is None
    assert await _bridge(redis, _event("unavailable", "on")) is None


@pytest.mark.asyncio
async def test_an_unreadable_decision_is_a_blip() -> None:
    redis = await _seeded("off")
    entry = _next_entry()
    redis.strings[f"{AVAILABILITY_DECISION_PREFIX}{entry.decode()}"] = "not json {"

    assert await _bridge(redis, _event("unavailable", "on"), entry) is None


# ---------------------------------------------------------------------------
# Wired into the runner, ahead of the attention gate
# ---------------------------------------------------------------------------


def _entry(event: StateChangedEvent) -> dict[bytes, bytes]:
    return {b"event": event.model_dump_json().encode()}


async def _process(
    redis: AsyncMock,
    event: StateChangedEvent,
    engine: AsyncMock,
    attention: AttentionSet | None = None,
    entry: bytes | None = None,
) -> bool:
    from core.reflex.runner import process_stream_entry

    return await process_stream_entry(
        entry_id=entry if entry is not None else _next_entry(),
        entry_data=_entry(event),
        engine=engine,
        agent=AsyncMock(),
        redis=redis,
        result_stream="alfred:home:action_results",
        observation_stream="alfred:reflex:observations",
        attention=attention,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("old", "new"), [("off", "unavailable"), ("unavailable", "off")])
async def test_a_blip_never_reaches_the_model_or_memory(old: str, new: str) -> None:
    redis = await _seeded("off")
    engine = AsyncMock()

    assert await _process(redis, _event(old, new), engine) is False
    engine.process_event.assert_not_awaited()
    redis.xadd.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_bridged_change_reaches_the_model_and_memory_as_one_change() -> None:
    redis = await _seeded("off")
    redis.set = AsyncMock(return_value=True)  # outside the observation debounce window
    engine = AsyncMock()
    engine.process_event = AsyncMock(return_value=None)

    await _process(redis, _event("unavailable", "on"), engine)

    assert _transition(engine.process_event.await_args.args[0]) == ("off", "on")
    obs = ReflexObservation.model_validate_json(redis.xadd.await_args.args[1]["event"])
    assert (obs.trigger_event["old_state"], obs.trigger_event["new_state"]) == ("off", "on")


@pytest.mark.asyncio
async def test_a_bridged_change_survives_a_failed_inference() -> None:
    """Left un-ACKed when the model is down, it must reach the model on the replay."""
    redis = await _seeded("off")
    returned, entry = _event("unavailable", "on"), _next_entry()
    engine = AsyncMock()
    engine.process_event = AsyncMock(side_effect=[ConnectionError("model down"), None, None])

    with pytest.raises(ConnectionError):
        await _process(redis, returned, engine, entry=entry)
    await _process(redis, _event("on", "on"), engine)  # an update lands in between
    await _process(redis, returned, engine, entry=entry)

    assert [_transition(c.args[0]) for c in engine.process_event.await_args_list] == [
        ("off", "on"),
        ("on", "on"),
        ("off", "on"),
    ]


@pytest.mark.asyncio
async def test_a_blip_does_not_start_the_attention_cooldown() -> None:
    """A real change right behind a blip must still fire.

    The gate's 5 s cooldown starts when an event passes it. Bridging after the gate
    would let `off → unavailable` start it and swallow the real change behind it.
    """
    from core.reflex.attention import AttentionSet

    redis = await _seeded("off", attention_redis({"alfred:attention:home": {ENTITY}}))
    attention = AttentionSet(redis=redis, cooldown_seconds=60.0)  # type: ignore[arg-type]
    engine = AsyncMock()
    engine.process_event = AsyncMock(return_value=None)

    await _process(redis, _event("off", "unavailable"), engine, attention)
    await _process(redis, _event("unavailable", "on"), engine, attention)

    engine.process_event.assert_awaited_once()
    assert _transition(engine.process_event.await_args.args[0]) == ("off", "on")
