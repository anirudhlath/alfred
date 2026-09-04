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


# ---------------------------------------------------------------------------
# Wiring into the runner's no-action path
# ---------------------------------------------------------------------------


def _entry(event: StateChangedEvent) -> dict[bytes, bytes]:
    return {b"event": event.model_dump_json().encode()}


@pytest.mark.asyncio
async def test_no_action_path_publishes_an_observation() -> None:
    from core.reflex.runner import process_stream_entry

    engine = AsyncMock()
    engine.process_event = AsyncMock(return_value=None)  # SLM: nothing to do
    agent = AsyncMock()
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)

    took_action = await process_stream_entry(
        entry_data=_entry(_event()),
        engine=engine,
        agent=agent,
        redis=redis,
        result_stream="alfred:home:action_results",
        observation_stream=STREAM,
    )

    assert took_action is False
    agent.execute_action.assert_not_awaited()
    redis.xadd.assert_awaited_once()
    stream_arg, fields = redis.xadd.await_args.args
    assert stream_arg == STREAM
    obs = ReflexObservation.model_validate_json(fields["event"])
    assert obs.action is None


@pytest.mark.asyncio
async def test_debounced_no_action_publishes_nothing() -> None:
    from core.reflex.runner import process_stream_entry

    engine = AsyncMock()
    engine.process_event = AsyncMock(return_value=None)
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=None)  # inside the window

    took_action = await process_stream_entry(
        entry_data=_entry(_event()),
        engine=engine,
        agent=AsyncMock(),
        redis=redis,
        result_stream="alfred:home:action_results",
        observation_stream=STREAM,
    )

    assert took_action is False
    redis.xadd.assert_not_awaited()


@pytest.mark.asyncio
async def test_attention_gated_event_is_not_observed() -> None:
    """The attention gate still comes first — gated events are not recorded."""
    from core.reflex.runner import process_stream_entry

    engine = AsyncMock()
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    attention = AsyncMock()
    attention.should_fire = AsyncMock(return_value=False)

    took_action = await process_stream_entry(
        entry_data=_entry(_event()),
        engine=engine,
        agent=AsyncMock(),
        redis=redis,
        result_stream="alfred:home:action_results",
        observation_stream=STREAM,
        attention=attention,
    )

    assert took_action is False
    engine.process_event.assert_not_awaited()
    redis.set.assert_not_awaited()
    redis.xadd.assert_not_awaited()


@pytest.mark.asyncio
async def test_action_path_observation_is_unchanged() -> None:
    """Regression: when the SLM acts, the observation still carries action+result."""
    from bus.schemas.events import ActionRequest, ActionResult
    from core.reflex.runner import process_stream_entry

    action = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="home.light_turn_on",
        parameters={"entity_id": "light.hallway"},
    )
    result = ActionResult(
        source="home-service",
        request_id=action.request_id,
        tool_name="home.light_turn_on",
        status="success",
    )

    engine = AsyncMock()
    engine.process_event = AsyncMock(return_value=action)
    agent = AsyncMock()
    agent.execute_action = AsyncMock(return_value=result)
    redis = AsyncMock()

    took_action = await process_stream_entry(
        entry_data=_entry(_event()),
        engine=engine,
        agent=agent,
        redis=redis,
        result_stream="alfred:home:action_results",
        observation_stream=STREAM,
    )

    assert took_action is True
    redis.set.assert_not_awaited()  # no debounce on the action path
    obs_call = [c for c in redis.xadd.await_args_list if c.args[0] == STREAM]
    assert len(obs_call) == 1
    obs = ReflexObservation.model_validate_json(obs_call[0].args[1]["event"])
    assert obs.action is not None
    assert obs.action.tool_name == "home.light_turn_on"
    assert obs.result is not None
