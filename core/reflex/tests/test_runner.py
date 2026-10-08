"""Tests for the Reflex Runner orchestration loop."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from bus.schemas.events import ActionRequest, ReflexProposal, StateChangedEvent
from shared.streams import OBSERVED_ENTITY_PREFIX


def _tv_event() -> StateChangedEvent:
    return StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="media_player.living_room_tv",
        old_state="paused",
        new_state="playing",
        attributes={"friendly_name": "Living Room TV"},
    )


def _act_proposal() -> ReflexProposal:
    return ReflexProposal(
        decision="act",
        reason="Film at night",
        action=ActionRequest(
            source="reflex-engine",
            target_service="home-service",
            tool_name="home.light_turn_on",
            parameters={"target": "Living Room", "brightness_pct": 30},
            reason="Film at night",
        ),
    )


async def _run(engine: AsyncMock, redis: AsyncMock, agent: AsyncMock | None = None) -> bool:
    from core.reflex.runner import process_stream_entry

    return await process_stream_entry(
        entry_id=b"1-0",
        entry_data={"event": _tv_event().model_dump_json()},
        engine=engine,
        agent=agent or AsyncMock(),
        redis=redis,
        result_stream="alfred:home:action_results",
        observation_stream="alfred:reflex:observations",
    )


@pytest.mark.asyncio
async def test_an_act_proposal_is_recorded_not_executed() -> None:
    from bus.schemas.events import ReflexObservation

    engine = AsyncMock()
    engine.process_event = AsyncMock(return_value=_act_proposal())
    agent = AsyncMock()
    redis = AsyncMock()

    took_action = await _run(engine, redis, agent)

    assert took_action is False
    agent.execute_action.assert_not_awaited()
    # Proposals skip the passive debounce: no observed-entity key is set.
    assert not any(
        str(c.args[0]).startswith(OBSERVED_ENTITY_PREFIX) for c in redis.set.await_args_list
    )
    (call,) = redis.xadd.await_args_list
    stream, fields = call.args
    assert stream == "alfred:reflex:observations"
    obs = ReflexObservation.model_validate_json(fields["event"])
    assert obs.origin == "state_change"
    assert obs.trigger_event["entity_id"] == "media_player.living_room_tv"
    assert obs.proposal is not None
    assert obs.proposal.decision == "act"
    assert obs.proposal.action is not None
    assert obs.proposal.action.tool_name == "home.light_turn_on"
    assert obs.action is None
    assert obs.result is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "proposal",
    [
        ReflexProposal(decision="ask", reason="Dim for the film?"),
        ReflexProposal(decision="invalid", raw="nope", problem="not JSON"),
    ],
)
async def test_ask_and_invalid_are_recorded_too(proposal: ReflexProposal) -> None:
    from bus.schemas.events import ReflexObservation

    engine = AsyncMock()
    engine.process_event = AsyncMock(return_value=proposal)
    redis = AsyncMock()

    assert await _run(engine, redis) is False

    (call,) = redis.xadd.await_args_list
    obs = ReflexObservation.model_validate_json(call.args[1]["event"])
    assert obs.proposal == proposal


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["act", "ask", "none", "invalid"])
async def test_every_decision_is_counted(decision: str) -> None:
    from core.reflex.runner import DECISION_COUNT_TTL_SECONDS

    engine = AsyncMock()
    engine.process_event = AsyncMock(
        return_value=ReflexProposal.model_validate({"decision": decision})
    )
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)

    await _run(engine, redis)

    (count,) = redis.hincrby.await_args_list
    key, field, amount = count.args
    assert key.startswith("alfred:reflex:decisions:")
    assert (field, amount) == (decision, 1)
    redis.expire.assert_awaited_once_with(key, DECISION_COUNT_TTL_SECONDS)


@pytest.mark.asyncio
async def test_count_decision_keys_by_utc_date() -> None:
    from core.reflex.runner import DECISION_COUNT_TTL_SECONDS, count_decision

    redis = AsyncMock()
    # 22:30 in Chicago on 7 Oct is already 8 Oct in UTC.
    await count_decision(redis, "ask", now=datetime(2026, 10, 8, 3, 30, tzinfo=UTC))

    redis.hincrby.assert_awaited_once_with("alfred:reflex:decisions:2026-10-08", "ask", 1)
    redis.expire.assert_awaited_once_with(
        "alfred:reflex:decisions:2026-10-08", DECISION_COUNT_TTL_SECONDS
    )
    assert DECISION_COUNT_TTL_SECONDS == 30 * 24 * 3600


@pytest.mark.asyncio
async def test_a_failed_count_does_not_block_the_ack() -> None:
    engine = AsyncMock()
    engine.process_event = AsyncMock(return_value=_act_proposal())
    redis = AsyncMock()
    redis.hincrby = AsyncMock(side_effect=Exception("OOM command not allowed"))

    assert await _run(engine, redis) is False
    redis.xadd.assert_awaited_once()  # the proposal is still recorded


@pytest.mark.asyncio
async def test_a_model_failure_propagates_so_the_entry_is_retried() -> None:
    engine = AsyncMock()
    engine.process_event = AsyncMock(side_effect=ConnectionError("model down"))

    with pytest.raises(ConnectionError):
        await _run(engine, AsyncMock())


@pytest.mark.asyncio
async def test_process_stream_entry_no_action_records_an_observation() -> None:
    """An event the SLM ignores is recorded passively, not dropped."""
    from core.reflex.runner import process_stream_entry

    event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="sensor.temperature",
        old_state="22.0",
        new_state="22.5",
    )

    mock_engine = AsyncMock()
    mock_engine.process_event.return_value = ReflexProposal(decision="none")

    mock_agent = AsyncMock()
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(return_value=True)

    result = await process_stream_entry(
        entry_id=b"1-0",
        entry_data={"event": event.model_dump_json()},
        engine=mock_engine,
        agent=mock_agent,
        redis=mock_redis,
        result_stream="alfred:home:action_results",
        observation_stream="alfred:reflex:observations",
    )

    assert result is False
    mock_engine.process_event.assert_called_once()
    mock_agent.execute_action.assert_not_called()
    # No action result — but the observation is recorded.
    streams_written = [c.args[0] for c in mock_redis.xadd.await_args_list]
    assert streams_written == ["alfred:reflex:observations"]


@pytest.mark.asyncio
async def test_process_stream_entry_malformed_event() -> None:
    """A malformed event should be logged and skipped, not crash."""
    from core.reflex.runner import process_stream_entry

    mock_engine = AsyncMock()
    mock_agent = AsyncMock()
    mock_redis = AsyncMock()

    result = await process_stream_entry(
        entry_id=b"1-0",
        entry_data={"event": "not valid json {{{"},
        engine=mock_engine,
        agent=mock_agent,
        redis=mock_redis,
        result_stream="alfred:home:action_results",
        observation_stream="alfred:reflex:observations",
    )

    assert result is False
    mock_engine.process_event.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_consumer_group_creates_if_missing() -> None:
    """Consumer group creation should be idempotent."""
    from core.reflex.runner import ensure_consumer_group

    mock_redis = AsyncMock()
    mock_redis.xgroup_create = AsyncMock()

    await ensure_consumer_group(mock_redis, "alfred:home:state_changed", "reflex-engine")

    mock_redis.xgroup_create.assert_called_once_with(
        "alfred:home:state_changed", "reflex-engine", id="0", mkstream=True
    )


@pytest.mark.asyncio
async def test_ensure_consumer_group_ignores_exists_error() -> None:
    """If consumer group already exists, should not raise."""
    import redis.asyncio as aioredis

    from core.reflex.runner import ensure_consumer_group

    mock_redis = AsyncMock()
    mock_redis.xgroup_create = AsyncMock(
        side_effect=aioredis.ResponseError("BUSYGROUP Consumer Group name already exists")
    )

    # Should not raise
    await ensure_consumer_group(mock_redis, "alfred:home:state_changed", "reflex-engine")


@pytest.mark.asyncio
async def test_process_stream_entry_handles_bytes_keys() -> None:
    """Redis returns bytes keys — verify they're handled."""
    from core.reflex.runner import process_stream_entry

    event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="sensor.temperature",
        old_state="22.0",
        new_state="22.5",
    )

    mock_engine = AsyncMock()
    mock_engine.process_event.return_value = ReflexProposal(decision="none")
    mock_agent = AsyncMock()
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(return_value=True)  # NX succeeds — see the sibling test

    result = await process_stream_entry(
        entry_id=b"1-0",
        entry_data={b"event": event.model_dump_json().encode()},
        engine=mock_engine,
        agent=mock_agent,
        redis=mock_redis,
        result_stream="alfred:home:action_results",
        observation_stream="alfred:reflex:observations",
    )

    assert result is False
    mock_engine.process_event.assert_called_once()
