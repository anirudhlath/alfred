"""Passive observation — recording events the Reflex Engine sees but ignores."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from bus.schemas.events import ReflexObservation, StateChangedEvent
from shared.streams import OBSERVED_ENTITY_PREFIX

STREAM = "alfred:reflex:observations"


def _event(entity_id: str = "media_player.living_room_tv") -> StateChangedEvent:
    return StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id=entity_id,
        old_state="paused",
        new_state="playing",
        attributes={"media_title": "Harry Potter"},
    )


@pytest.mark.asyncio
async def test_first_event_for_entity_is_published() -> None:
    from core.reflex.runner import observe_passively

    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)  # NX succeeded — not seen recently

    published = await observe_passively(redis, STREAM, _event())

    assert published is True
    redis.xadd.assert_awaited_once()
    stream_arg, fields = redis.xadd.await_args.args
    assert stream_arg == STREAM
    obs = ReflexObservation.model_validate_json(fields["event"])
    assert obs.action is None
    assert obs.result is None
    assert obs.origin == "state_change"
    assert obs.trigger_event["entity_id"] == "media_player.living_room_tv"
    assert obs.trigger_event["new_state"] == "playing"


@pytest.mark.asyncio
async def test_repeat_within_window_is_skipped() -> None:
    from core.reflex.runner import observe_passively

    redis = AsyncMock()
    redis.set = AsyncMock(return_value=None)  # NX failed — key already present

    published = await observe_passively(redis, STREAM, _event())

    assert published is False
    redis.xadd.assert_not_awaited()


@pytest.mark.asyncio
async def test_debounce_key_is_per_entity_with_ttl() -> None:
    from core.reflex.runner import OBSERVATION_DEBOUNCE_SECONDS, observe_passively

    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)

    await observe_passively(redis, STREAM, _event("light.kitchen"))

    key = redis.set.await_args.args[0]
    assert key == f"{OBSERVED_ENTITY_PREFIX}light.kitchen"
    assert redis.set.await_args.kwargs["nx"] is True
    assert redis.set.await_args.kwargs["ex"] == OBSERVATION_DEBOUNCE_SECONDS


@pytest.mark.asyncio
async def test_distinct_entities_do_not_share_a_window() -> None:
    """Cross-entity sequences (TV on, then lights down) must stay visible."""
    from core.reflex.runner import observe_passively

    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)

    await observe_passively(redis, STREAM, _event("media_player.tv"))
    await observe_passively(redis, STREAM, _event("light.living_room"))

    keys = [c.args[0] for c in redis.set.await_args_list]
    assert keys == [
        f"{OBSERVED_ENTITY_PREFIX}media_player.tv",
        f"{OBSERVED_ENTITY_PREFIX}light.living_room",
    ]
    assert redis.xadd.await_count == 2


@pytest.mark.asyncio
async def test_event_after_ttl_expiry_is_published_again() -> None:
    """Once the key expires, the same entity records again."""
    from core.reflex.runner import observe_passively

    redis = AsyncMock()
    # First call sets the key; second is inside the window; third is after expiry.
    redis.set = AsyncMock(side_effect=[True, None, True])

    first = await observe_passively(redis, STREAM, _event())
    second = await observe_passively(redis, STREAM, _event())
    third = await observe_passively(redis, STREAM, _event())

    assert (first, second, third) == (True, False, True)
    assert redis.xadd.await_count == 2


@pytest.mark.asyncio
async def test_debounce_window_is_overridable() -> None:
    from core.reflex.runner import observe_passively

    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)

    await observe_passively(redis, STREAM, _event(), debounce_seconds=42)

    assert redis.set.await_args.kwargs["ex"] == 42


@pytest.mark.asyncio
async def test_attributes_survive_into_the_payload() -> None:
    """The ingestor reads salient attributes out of trigger_event."""
    from core.reflex.runner import observe_passively

    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)

    await observe_passively(redis, STREAM, _event())

    fields = redis.xadd.await_args.args[1]
    payload = json.loads(fields["event"])
    assert payload["trigger_event"]["attributes"]["media_title"] == "Harry Potter"
