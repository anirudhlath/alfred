"""Availability bridging — `unavailable`/`unknown` carry no information.

A device dropping off the network and coming back is not something that happened
at home. The bridge compares the state an entity comes back in with the last real
state it had: the same state is a blip and is dropped, a different one is a single
real change. It runs before the attention gate, so a blip costs no inference and
starts no cooldown.

What the bridge remembers is checked through what it does next, never by reading
the hash, so the tests hold whatever the stored shape is.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from bus.schemas.events import ReflexObservation, StateChangedEvent
from shared.streams import LAST_KNOWN_STATE_KEY
from tests.helpers import attention_redis

if TYPE_CHECKING:
    from core.reflex.attention import AttentionSet

ENTITY = "media_player.shield"


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
    """Back HGET/HSET with a dict. HGET hands back bytes, as the production pool does."""
    store: dict[str, dict[str, str]] = {}

    async def _hget(key: str, field: str) -> bytes | None:
        value = store.get(key, {}).get(field)
        return None if value is None else value.encode()

    async def _hset(key: str, field: str, value: str) -> int:
        store.setdefault(key, {})[field] = value
        return 1

    redis.hashes = store
    redis.hget = AsyncMock(side_effect=_hget)
    redis.hset = AsyncMock(side_effect=_hset)
    return redis


async def _bridge(redis: AsyncMock, event: StateChangedEvent) -> StateChangedEvent | None:
    from core.reflex.availability import bridge_availability

    return await bridge_availability(redis, event)


async def _seeded(state: str, redis: AsyncMock | None = None) -> AsyncMock:
    """A hash that has seen ENTITY settle in ``state`` through a real change."""
    redis = _with_hash(redis if redis is not None else AsyncMock())
    assert await _bridge(redis, _event(None, state)) is not None
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
    # The rest of the event — and above all its timestamp — is the HA event's own.
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
async def test_unknown_to_unavailable_is_dropped_and_forgets_nothing() -> None:
    redis = await _seeded("on")

    assert await _bridge(redis, _event("unknown", "unavailable")) is None
    assert await _bridge(redis, _event("unavailable", "on")) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("old", ["off", None])
async def test_a_real_change_passes_unchanged_and_is_remembered(old: str | None) -> None:
    redis = _with_hash(AsyncMock())
    event = _event(old, "on")

    assert await _bridge(redis, event) is event
    assert _transition(await _bridge(redis, _event("unavailable", "off"))) == ("on", "off")


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
# model down, a tool call raising). The bridge has already recorded the event by
# then, so a replay must not be compared against the event's own state.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_redelivered_return_is_bridged_again() -> None:
    redis = await _seeded("off")
    event = _event("unavailable", "on")

    assert _transition(await _bridge(redis, event)) == ("off", "on")
    assert _transition(await _bridge(redis, event)) == ("off", "on")


@pytest.mark.asyncio
async def test_a_redelivered_blip_stays_a_blip() -> None:
    redis = await _seeded("off")
    event = _event("unavailable", "off")

    assert await _bridge(redis, event) is None
    assert await _bridge(redis, event) is None


@pytest.mark.asyncio
async def test_only_the_same_event_is_treated_as_a_replay() -> None:
    """A fresh return in the state the last one left is a blip, not a second change."""
    redis = await _seeded("off")

    assert _transition(await _bridge(redis, _event("unavailable", "on"))) == ("off", "on")
    assert await _bridge(redis, _event("on", "unavailable")) is None
    assert await _bridge(redis, _event("unavailable", "on")) is None


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
) -> bool:
    from core.reflex.runner import process_stream_entry

    return await process_stream_entry(
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
    entry = _event("unavailable", "on")
    engine = AsyncMock()
    engine.process_event = AsyncMock(side_effect=[ConnectionError("model down"), None])

    with pytest.raises(ConnectionError):
        await _process(redis, entry, engine)
    await _process(redis, entry, engine)

    assert [_transition(c.args[0]) for c in engine.process_event.await_args_list] == [
        ("off", "on"),
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
