"""Every Reflex decision ends in one outcome, and a reversal of a person's change is not proposed.

The model answers; the code then reduces the answer to an outcome (``core/reflex/outcome.py``),
reading what a person just changed (``core/reflex/reversal.py``). The runner records the
decision with its outcome, counts it and publishes it. Ids and names are the eval apartment
world's; the tool names are test data.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import fakeredis
import pytest

from bus.schemas.events import (
    ActionRequest,
    ChangeContext,
    ReflexObservation,
    ReflexOutcome,
    ReflexProposal,
    StateChangedEvent,
    TriggerFired,
)
from core.reflex.__main__ import _handle_trigger_fired
from core.reflex.decision_log import DecisionLog
from core.reflex.engine import ReflexEngine
from core.reflex.outcome import first_outcome, publishes
from core.reflex.reversal import PersonChanges
from core.reflex.runner import (
    count_outcome,
    decisions_key,
    process_stream_entry,
    publish_proposal,
    record_decision,
)
from core.reflex.tool_registry import ToolInfo
from sdk.alfred_sdk.context import ContextEntry, ContextSnapshot
from shared.streams import (
    REFLEX_OBSERVATIONS_STREAM,
    REFLEX_PERSON_CHANGES_KEY,
)
from tests.core.reflex.fakes import decided, reflex_agent

if TYPE_CHECKING:
    from shared.types import AioRedis
    from tests.llm_fakes import Reply, Script

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
LAMP = "light.living_room_lamp"
TV = "media_player.living_room_tv"
SOON = NOW - timedelta(minutes=1)
RESULT_STREAM = "alfred:home:action_results"

TURN_LAMP_ON = json.dumps(
    {
        "decision": "act",
        "reason": "Dim light for a film",
        "tool_name": "home.light_turn_on",
        "target_service": "home-service",
        "parameters": {"target": LAMP},
    }
)
TURN_HALL_ON = json.dumps(
    {
        "decision": "act",
        "reason": "Light for the hall",
        "tool_name": "home.light_turn_on",
        "target_service": "home-service",
        "parameters": {"target": "light.hall"},
    }
)
NONE = json.dumps({"decision": "none"})

LIGHT_TURN_ON = ToolInfo(
    name="home.light_turn_on",
    description="light.turn_on",
    feature_name="home",
    feature_description="",
    target_service="home-service",
    audience="reflex",
    input_schema={
        "type": "object",
        "properties": {"target": {"type": "string"}},
        "required": ["target"],
    },
)


def _action(tool_name: str = "home.light_turn_on") -> ActionRequest:
    return ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name=tool_name,
        parameters={"target": LAMP},
    )


def _snapshot() -> ContextSnapshot:
    def light(entity_id: str, name: str, area: str | None) -> ContextEntry:
        attributes = {"friendly_name": name, **({"area": area} if area else {})}
        return ContextEntry(entity_id=entity_id, state="off", attributes=attributes)

    return ContextSnapshot(
        controllable={
            "light": [
                light(LAMP, "Lamp", "Living room"),
                light("light.hall", "Hall light", None),
            ]
        },
        sensors={},
    )


def _engine(redis: AioRedis, *replies: Reply) -> tuple[ReflexEngine, PersonChanges, Script]:
    """An engine that reads what people changed from *redis* and answers with *replies*."""
    registry = AsyncMock()
    registry.get_tools = AsyncMock(return_value=[LIGHT_TURN_ON])
    reader = AsyncMock()
    reader.get_snapshot = AsyncMock(return_value=_snapshot())
    reader.get_user_timezone = AsyncMock(return_value="UTC")
    agent, script = reflex_agent(*replies)
    changes = PersonChanges(redis, clock=lambda: NOW)
    engine = ReflexEngine(
        preferences_dir="/unused",
        tool_registry=registry,
        context_reader=reader,
        agent=agent,
        clock=lambda: NOW,
        redis=redis,
        person_changes=changes,
    )
    engine._cached_preferences = ""
    return engine, changes, script


def _change(
    entity_id: str, old: str, new: str, *, context: ChangeContext | None = None
) -> StateChangedEvent:
    return StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id=entity_id,
        old_state=old,
        new_state=new,
        context=context or ChangeContext(id="ctx-1", user_id="user-1"),
        timestamp=SOON,
    )


async def _deliver(
    redis: AioRedis,
    engine: ReflexEngine,
    event: StateChangedEvent,
    *,
    log: DecisionLog | None = None,
    changes: PersonChanges | None = None,
    attention: AsyncMock | None = None,
) -> bool:
    return await process_stream_entry(
        entry_id=b"1760000000000-0",
        entry_data={b"event": event.model_dump_json().encode()},
        engine=engine,
        agent=AsyncMock(),
        redis=redis,
        result_stream=RESULT_STREAM,
        observation_stream=REFLEX_OBSERVATIONS_STREAM,
        attention=attention,
        decision_log=log,
        person_changes=changes,
    )


async def _counted(redis: AioRedis) -> dict[str, int]:
    counts = await redis.hgetall(decisions_key(datetime.now(UTC).date()))
    return {k.decode(): int(v) for k, v in counts.items()}


async def _observed(redis: AioRedis) -> list[ReflexObservation]:
    entries = await redis.xrange(REFLEX_OBSERVATIONS_STREAM)
    return [ReflexObservation.model_validate_json(fields[b"event"]) for _, fields in entries]


# --- the outcome of a proposal ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("proposal", "reversed_", "expected"),
    [
        (ReflexProposal(decision="none"), [], ReflexOutcome(kind="none")),
        (ReflexProposal(decision="none"), [LAMP], ReflexOutcome(kind="none")),
        (
            ReflexProposal(decision="invalid", raw="x", problem="not JSON"),
            [],
            ReflexOutcome(kind="invalid"),
        ),
        (
            ReflexProposal(decision="act", action=_action()),
            [],
            ReflexOutcome(kind="shadow"),
        ),
        (
            ReflexProposal(decision="ask", reason="Dim?"),
            [],
            ReflexOutcome(kind="shadow"),
        ),
        (
            ReflexProposal(decision="act", action=_action()),
            [LAMP],
            ReflexOutcome(kind="would_reverse", reversed=[LAMP]),
        ),
        (
            ReflexProposal(decision="ask", reason="Dim?", action=_action()),
            [LAMP, TV],
            ReflexOutcome(kind="would_reverse", reversed=[LAMP, TV]),
        ),
    ],
)
def test_first_outcome(
    proposal: ReflexProposal, reversed_: list[str], expected: ReflexOutcome
) -> None:
    assert first_outcome(proposal, reversed_) == expected


@pytest.mark.parametrize(
    ("kind", "published"),
    [
        ("none", False),
        ("would_reverse", False),
        ("shadow", True),
        ("invalid", True),
        ("asked", True),
        ("held", True),
    ],
)
def test_only_some_outcomes_are_published_as_proposals(kind: str, published: bool) -> None:
    assert publishes(ReflexOutcome.model_validate({"kind": kind})) is published


# --- the state path ---------------------------------------------------------------------------


async def test_a_reversing_proposal_becomes_would_reverse() -> None:
    redis = fakeredis.FakeAsyncRedis()
    log = DecisionLog(redis, clock=lambda: NOW)
    engine, changes, script = _engine(redis, TURN_LAMP_ON)
    # A person turned the lamp off; the model, asked about that, proposes turning it on.
    event = _change(LAMP, "on", "off")

    await _deliver(redis, engine, event, log=log, changes=changes)

    (record,) = await log.read(since=NOW - timedelta(days=1))
    assert record.proposal.decision == "act"
    assert record.outcome == ReflexOutcome(kind="would_reverse", reversed=[LAMP])
    assert list(record.recent) == [LAMP]
    assert record.recent[LAMP].new_state == "off"
    assert record.recent[LAMP].cause == "person"
    assert record.prompt == script.prompt()
    # Nothing is published as a proposal: it is observed as a passive "none" is.
    assert [o.proposal for o in await _observed(redis)] == [None]
    counted = await _counted(redis)
    assert counted["outcome:would_reverse"] == 1
    assert counted["act"] == 1  # what the model said is still counted


async def test_a_proposal_that_reverses_nothing_is_published_with_its_outcome() -> None:
    redis = fakeredis.FakeAsyncRedis()
    log = DecisionLog(redis, clock=lambda: NOW)
    engine, changes, _ = _engine(redis, TURN_HALL_ON)

    await _deliver(redis, engine, _change(LAMP, "on", "off"), log=log, changes=changes)

    (record,) = await log.read(since=NOW - timedelta(days=1))
    assert record.outcome == ReflexOutcome(kind="shadow")
    (observation,) = await _observed(redis)
    assert observation.proposal is not None
    assert observation.proposal.decision == "act"
    assert observation.outcome == ReflexOutcome(kind="shadow")
    counted = await _counted(redis)
    assert counted["outcome:shadow"] == 1
    assert "outcome:would_reverse" not in counted


async def test_an_invalid_reply_is_published_with_the_invalid_outcome() -> None:
    redis = fakeredis.FakeAsyncRedis()
    engine, changes, _ = _engine(redis, "Sure! Turning it on.")

    await _deliver(redis, engine, _change(LAMP, "on", "off"), changes=changes)

    (observation,) = await _observed(redis)
    assert observation.proposal is not None
    assert observation.proposal.decision == "invalid"
    assert observation.outcome == ReflexOutcome(kind="invalid")
    assert (await _counted(redis))["outcome:invalid"] == 1


async def test_a_none_is_observed_passively_with_no_outcome_on_the_observation() -> None:
    redis = fakeredis.FakeAsyncRedis()
    log = DecisionLog(redis, clock=lambda: NOW)
    engine, changes, _ = _engine(redis, NONE)

    await _deliver(redis, engine, _change(LAMP, "on", "off"), log=log, changes=changes)

    (record,) = await log.read(since=NOW - timedelta(days=1))
    assert record.outcome == ReflexOutcome(kind="none")
    assert [o.proposal for o in await _observed(redis)] == [None]
    assert (await _counted(redis))["outcome:none"] == 1


async def test_the_runner_records_each_decision_with_the_prompt_the_model_saw() -> None:
    redis = fakeredis.FakeAsyncRedis()
    log = DecisionLog(redis, clock=lambda: NOW)
    engine, changes, script = _engine(redis, TURN_HALL_ON, TURN_HALL_ON)
    event = _change(LAMP, "on", "off")

    await _deliver(redis, engine, event, log=log, changes=changes)
    trigger = TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time")
    await _handle_trigger_fired(
        {"event": trigger.model_dump_json()}, engine, AsyncMock(), redis, AsyncMock(),
        decision_log=log,
    )  # fmt: skip

    records = await log.read(since=NOW - timedelta(days=1))
    assert [r.origin for r in records] == ["state_change", "trigger_fired"]
    assert [r.prompt for r in records] == [script.prompt(0), script.prompt(1)]
    assert records[0].event["entity_id"] == LAMP
    assert records[1].cause is None


async def test_without_a_log_nothing_is_recorded_and_the_decision_still_counts() -> None:
    redis = fakeredis.FakeAsyncRedis()
    engine, changes, _ = _engine(redis, NONE)

    await _deliver(redis, engine, _change(LAMP, "on", "off"), changes=changes)

    assert (await _counted(redis))["none"] == 1


# --- which events are noted -------------------------------------------------------------------


async def test_a_house_device_change_is_noted_even_when_the_gate_drops_it() -> None:
    redis = fakeredis.FakeAsyncRedis()
    engine, changes, _ = _engine(redis)
    attention = AsyncMock()
    attention.should_fire = AsyncMock(return_value=False)

    await _deliver(redis, engine, _change(LAMP, "on", "off"), changes=changes, attention=attention)

    attention.should_fire.assert_awaited_once()
    assert list(await changes.recent(timedelta(hours=1))) == [LAMP]


async def test_a_sensor_update_is_not_noted() -> None:
    redis = fakeredis.FakeAsyncRedis()
    engine, changes, _ = _engine(redis, NONE)
    sensor = _change("sensor.living_room_temperature", "20.5", "20.6", context=ChangeContext())

    await _deliver(redis, engine, sensor, changes=changes)

    assert await redis.hlen(REFLEX_PERSON_CHANGES_KEY) == 0


async def test_a_state_path_without_person_changes_notes_nothing() -> None:
    redis = fakeredis.FakeAsyncRedis()
    engine, _, _ = _engine(redis, NONE)

    await _deliver(redis, engine, _change(LAMP, "on", "off"))

    assert await redis.hlen(REFLEX_PERSON_CHANGES_KEY) == 0


# --- the trigger path -------------------------------------------------------------------------


async def test_trigger_fired_proposals_are_checked_too() -> None:
    redis = fakeredis.FakeAsyncRedis()
    log = DecisionLog(redis, clock=lambda: NOW)
    engine, changes, _ = _engine(redis, TURN_LAMP_ON)
    await changes.note(_change(LAMP, "on", "off"), "person")
    trigger = TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time")
    publisher = AsyncMock()

    await _handle_trigger_fired(
        {"event": trigger.model_dump_json()}, engine, AsyncMock(), redis, publisher,
        decision_log=log,
    )  # fmt: skip

    (record,) = await log.read(since=NOW - timedelta(days=1))
    assert record.origin == "trigger_fired"
    assert record.outcome == ReflexOutcome(kind="would_reverse", reversed=[LAMP])
    assert list(record.recent) == [LAMP]
    assert await redis.xlen(REFLEX_OBSERVATIONS_STREAM) == 0
    publisher.publish.assert_awaited_once()  # Path A is not touched
    counted = await _counted(redis)
    assert counted["outcome:would_reverse"] == 1
    assert counted["act"] == 1


async def test_a_trigger_proposal_that_reverses_nothing_is_published_with_its_outcome() -> None:
    redis = fakeredis.FakeAsyncRedis()
    engine, changes, _ = _engine(redis, TURN_HALL_ON)
    await changes.note(_change(LAMP, "on", "off"), "person")
    trigger = TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time")

    await _handle_trigger_fired(
        {"event": trigger.model_dump_json()}, engine, AsyncMock(), redis, AsyncMock()
    )

    (observation,) = await _observed(redis)
    assert observation.origin == "trigger_fired"
    assert observation.outcome == ReflexOutcome(kind="shadow")


# --- the counts -------------------------------------------------------------------------------


async def test_record_decision_logs_then_counts_the_decision_then_the_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one step both origins take: the same three writes, in the same order."""
    from core.reflex import runner

    order: list[str] = []
    log = AsyncMock()
    log.record.side_effect = lambda *_: order.append("log")
    monkeypatch.setattr(
        runner, "count_decision", AsyncMock(side_effect=lambda *_: order.append("decision"))
    )
    monkeypatch.setattr(
        runner, "count_outcome", AsyncMock(side_effect=lambda *_: order.append("outcome"))
    )
    event = _change(LAMP, "on", "off")
    result = decided(ReflexProposal(decision="none"))

    await record_decision(AsyncMock(), log, "state_change", event, result)

    assert order == ["log", "decision", "outcome"]
    log.record.assert_awaited_once_with("state_change", event, result)


async def test_record_decision_without_a_log_still_counts() -> None:
    redis = fakeredis.FakeAsyncRedis()

    await record_decision(
        redis,
        None,
        "trigger_fired",
        _change(LAMP, "on", "off"),
        decided(ReflexProposal(decision="invalid", raw="nope", problem="not JSON")),
    )

    counted = await redis.hgetall(decisions_key(datetime.now(UTC).date()))
    assert {k.decode(): int(v) for k, v in counted.items()} == {"invalid": 1, "outcome:invalid": 1}


async def test_count_outcome_counts_the_kind_and_the_reason() -> None:
    redis = fakeredis.FakeAsyncRedis()

    await count_outcome(redis, ReflexOutcome(kind="shadow"))
    await count_outcome(redis, ReflexOutcome(kind="held", reason="tool_off"))
    await count_outcome(redis, ReflexOutcome(kind="held", reason="tool_off"))
    await count_outcome(redis, ReflexOutcome(kind="held", reason="dnd"))

    assert await _counted(redis) == {
        "outcome:shadow": 1,
        "outcome:held": 3,
        "held:tool_off": 2,
        "held:dnd": 1,
    }
    assert 0 < await redis.ttl(decisions_key(datetime.now(UTC).date())) <= 30 * 24 * 3600


async def test_count_outcome_keys_by_utc_date() -> None:
    redis = AsyncMock()

    await count_outcome(
        redis, ReflexOutcome(kind="none"), now=datetime(2026, 10, 8, 3, 30, tzinfo=UTC)
    )

    redis.hincrby.assert_awaited_once_with("alfred:reflex:decisions:2026-10-08", "outcome:none", 1)


async def test_a_failed_outcome_count_never_raises() -> None:
    redis = AsyncMock()
    redis.hincrby = AsyncMock(side_effect=Exception("OOM command not allowed"))

    await count_outcome(redis, ReflexOutcome(kind="shadow"))


# --- the observation --------------------------------------------------------------------------


async def test_a_published_proposal_carries_its_outcome() -> None:
    redis = fakeredis.FakeAsyncRedis()
    proposal = ReflexProposal(decision="act", action=_action())
    event = _change(LAMP, "on", "off")

    await publish_proposal(
        redis,
        REFLEX_OBSERVATIONS_STREAM,
        "state_change",
        event,
        proposal,
        outcome=ReflexOutcome(kind="shadow"),
    )

    (observation,) = await _observed(redis)
    assert observation.proposal == proposal
    assert observation.outcome == ReflexOutcome(kind="shadow")


async def test_a_proposal_published_without_an_outcome_has_none() -> None:
    redis = fakeredis.FakeAsyncRedis()

    await publish_proposal(
        redis,
        REFLEX_OBSERVATIONS_STREAM,
        "state_change",
        _change(LAMP, "on", "off"),
        ReflexProposal(decision="ask", reason="Dim?"),
    )

    (observation,) = await _observed(redis)
    assert observation.outcome is None


def test_an_observation_written_before_outcomes_still_parses() -> None:
    old = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event={"entity_id": LAMP},
        proposal=ReflexProposal(decision="ask", reason="Dim?"),
    )
    payload = json.loads(old.model_dump_json())
    del payload["outcome"]

    parsed = ReflexObservation.model_validate(payload)

    assert parsed.outcome is None
    assert parsed.proposal == old.proposal


def test_an_outcome_defaults_to_no_reason_no_entities_and_no_ask() -> None:
    outcome = ReflexOutcome(kind="shadow")

    assert (outcome.reason, outcome.reversed, outcome.ask_id) == (None, [], None)


def test_a_held_outcome_names_one_of_the_reasons() -> None:
    assert ReflexOutcome(kind="held", reason="quiet_hours").reason == "quiet_hours"
    with pytest.raises(ValueError, match="reason"):
        ReflexOutcome.model_validate({"kind": "held", "reason": "bored"})
