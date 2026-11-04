"""Records and a small house to label: shared by the replay's command and label tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from bus.schemas.events import (
    ActionRequest,
    ChangeContext,
    ReflexDecision,
    ReflexOutcome,
    ReflexProposal,
    StateChangedEvent,
    TriggerFired,
)
from core.attribution.cause import cause_of
from core.reflex.decision_log import DecisionRecord
from core.reflex.prompt import LiveEntity, build_state_change_prompt, build_trigger_prompt
from core.reflex.prose import repo_prose
from core.reflex.reversal import PersonChange
from core.reflex.tool_registry import ToolInfo

LAMP_ON = ToolInfo(
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

DAY = datetime(2026, 10, 27, 14, 0, tzinfo=UTC)  # mid-afternoon: outside the default quiet hours
LAMP = "light.living_room_lamp"
PENDANTS = "light.kitchen_pendants"
BEDROOM = "light.bedroom_lamp"
MOTION = "binary_sensor.kitchen_motion"  # not a device Reflex notes a person's change to
PERSON = ChangeContext(id="ctx-person", user_id="user-1")
ALFRED = ChangeContext(id="ctx-alfred", by_service=True)


def live_entity(entity_id: str, name: str, area: str) -> LiveEntity:
    return LiveEntity(
        entity_id=entity_id,
        domain=entity_id.split(".")[0],
        controllable=True,
        state="off",
        attributes={"friendly_name": name, "area": area},
    )


HOUSE = {
    e.entity_id: e
    for e in (
        live_entity(LAMP, "Living Room Lamp", "Living Room"),
        live_entity(PENDANTS, "Kitchen Pendants", "Kitchen"),
        live_entity(BEDROOM, "Bedroom Lamp", "Bedroom"),
    )
}


def minutes(count: int) -> timedelta:
    return timedelta(minutes=count)


def person_change(at: datetime) -> PersonChange:
    return PersonChange(
        time=at,
        cause="person",
        old_state="off",
        new_state="on",
        context_id="ctx-person",
        attributes_only=False,
    )


def record_at(
    at: datetime,
    *,
    entity: str = MOTION,
    old: str | None = "off",
    new: str = "on",
    context: ChangeContext | None = None,
    decision: ReflexDecision = "none",
    target: str | None = None,
    outcome: ReflexOutcome | None = None,
    recent: dict[str, PersonChange] | None = None,
) -> DecisionRecord:
    """A decision about a state change at *at*; one with a *target* acts through the lamp tool."""
    event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id=entity,
        old_state=old,
        new_state=new,
        context=context,
        timestamp=at - timedelta(seconds=2),
    )
    prompt = build_state_change_prompt(
        event=event,
        preferences="",
        tools=[LAMP_ON],
        entities=None,
        now=at,
        tz_name="UTC",
        prose=repo_prose("state_change"),
    )
    action = (
        None
        if target is None
        else ActionRequest(
            source="reflex-engine",
            target_service="home-service",
            tool_name=LAMP_ON.name,
            parameters={"target": target},
        )
    )
    return DecisionRecord(
        time=at,
        origin="state_change",
        event=event.model_dump(mode="json"),
        cause=cause_of(context),
        prompt=prompt,
        proposal=ReflexProposal(decision=decision, reason="the lamp was left on", action=action),
        outcome=outcome,
        recent=recent or {},
    )


def fired_at(at: datetime, *, decision: ReflexDecision, target: str) -> DecisionRecord:
    """A decision about a fired trigger, which carries no state change to fold."""
    event = TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time")
    prompt = build_trigger_prompt(
        event=event,
        preferences="",
        tools=[LAMP_ON],
        entities=None,
        now=at,
        tz_name="UTC",
        prose=repo_prose("trigger_fired"),
    )
    action = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name=LAMP_ON.name,
        parameters={"target": target},
    )
    return DecisionRecord(
        time=at,
        origin="trigger_fired",
        event=event.model_dump(mode="json"),
        cause=None,
        prompt=prompt,
        proposal=ReflexProposal(decision=decision, reason="the lamp was left on", action=action),
    )


def act_reply(target: str) -> str:
    return json.dumps(
        {
            "decision": "act",
            "tool_name": "home.light_turn_on",
            "parameters": {"target": target},
            "reason": "dark",
        }
    )


def standard_log() -> list[DecisionRecord]:
    """A reversal, a repeat, quiet hours and asks, oldest first."""
    return [
        # A person turns the bedroom lamp on; nothing is proposed.
        record_at(DAY, entity=BEDROOM, new="on", context=PERSON),
        # Ten minutes on, the model would switch that same lamp: it reverses the person.
        record_at(DAY + minutes(10), decision="act", target="Bedroom Lamp"),
        record_at(DAY + minutes(20), decision="ask", target="Kitchen Pendants"),  # an ask
        record_at(DAY + minutes(30), decision="act", target="Living Room Lamp"),  # another target
        record_at(DAY + minutes(50), decision="act", target="Kitchen Pendants"),  # a repeat
        record_at(DAY + minutes(200), decision="act", target="Kitchen Pendants"),  # window over
        record_at(DAY + minutes(210), decision="act", target="Garage Light"),  # no such light
        record_at(DAY + minutes(215), decision="invalid"),  # the model's reply was no decision
        record_at(DAY + minutes(570), decision="act", target="Living Room Lamp"),  # 23:30
    ]
