from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from bus.schemas.events import ActionRequest, ServiceRegistered, TriggerCreated, TriggerFired
from core.notifications.schema import Notification, Urgency
from core.reflex.tool_registry import ToolInfo
from core.triggers.models import TRIGGER_ENGINE_SOURCE, TRIGGER_TITLE_PREFIX
from evals.harness.bus import Entry
from evals.harness.checks import run_check
from evals.harness.checks.triggers import TriggerFiredParams
from evals.harness.collect import (
    deferred_records,
    is_fire_of,
    notification_records,
    trigger_records,
)
from evals.harness.errors import HarnessError
from evals.harness.evidence import LlmCall
from evals.harness.reflex import local_hour, reflex_calls, targets, tool_domain
from evals.harness.world import World, load_world
from tests.evals.harness.factories import evidence

WALL0 = 1_760_000_000.0  # time.time() when the sample started
STARTED = 50.0  # time.monotonic() at the same instant
CREATED = datetime(2026, 10, 8, 18, 0, tzinfo=UTC)


def event_entry(
    event: TriggerCreated | TriggerFired | ActionRequest | ServiceRegistered, wall: float
) -> Entry:
    return Entry(wall=wall, data={"event": event.model_dump_json()})


def made(
    created_by: str = "tool-call", trigger_id: str = "t1", action: dict[str, object] | None = None
) -> TriggerCreated:
    return TriggerCreated(
        trigger_id=trigger_id,
        trigger_type="time",
        name="Laundry",
        created_by=created_by,
        conditions={"run_at": "2026-10-08T12:20:00-06:00"},
        action=action,
        urgency="urgent",
        timestamp=CREATED,
    )


LAMP = {
    "tool_name": "home.light_turn_on",
    "target_service": "home-service",
    "parameters": {"target": "light.bedroom_lamp"},
}


def engine_runs(action: dict[str, object] = LAMP, **fields: object) -> ActionRequest:
    """The ActionRequest the trigger engine sends for a trigger with *action* (engine.py)."""
    return ActionRequest.model_validate({"source": "trigger-engine", **action, **fields})


def test_trigger_records_keep_system2s_triggers_and_every_fire() -> None:
    fired = TriggerFired(trigger_id="t1", trigger_name="Laundry", trigger_type="time")
    entries = [
        event_entry(made(), WALL0 + 2),
        event_entry(made(created_by="notification-dispatcher"), WALL0 + 3),
        event_entry(fired, WALL0 + 9),
        Entry(wall=WALL0 + 4, data={"event": json.dumps({"event_type": "service_registered"})}),
        Entry(wall=WALL0 + 5, data={"event": "{not json"}),
        Entry(wall=WALL0 + 6, data={"other": "x"}),
    ]
    created, fires = trigger_records(entries, STARTED, WALL0)
    [record] = created
    assert record.t == STARTED + 2 and record.urgency == "urgent"
    assert record.conditions == {"run_at": "2026-10-08T12:20:00-06:00"}
    assert record.created_at == CREATED
    [fire] = fires
    assert (fire.t, fire.name, fire.fired_by) == (STARTED + 9, "Laundry", "engine")


def test_trigger_records_are_this_samples_earliest_first() -> None:
    """Checks take ``[0]`` as the earliest record and count only this sample's triggers."""
    fired = TriggerFired(trigger_id="t1", trigger_name="Laundry", trigger_type="time")
    entries = [
        event_entry(made(trigger_id="late"), WALL0 + 8),
        event_entry(made(trigger_id="early"), WALL0 + 1),
        event_entry(made(trigger_id="at start"), WALL0),
        event_entry(made(trigger_id="before"), WALL0 - 0.5),  # the previous sample's
        event_entry(fired, WALL0 + 9),
        event_entry(fired, WALL0 + 3),
        event_entry(fired, WALL0 - 1),
    ]
    created, fires = trigger_records(entries, STARTED, WALL0)
    assert [(r.trigger_id, r.t) for r in created] == [
        ("at start", STARTED),
        ("early", STARTED + 1),
        ("late", STARTED + 8),
    ]
    assert [f.t for f in fires] == [STARTED + 3, STARTED + 9]


def test_the_window_keeps_the_starts_own_millisecond() -> None:
    """Stream ids are whole milliseconds, and the bus reads from the start's own one, so
    the collectors keep it too: one rule on both sides."""
    started_wall = WALL0 + 0.0004  # inside the millisecond that begins at WALL0
    entries = [
        event_entry(made(trigger_id="same ms"), WALL0),
        event_entry(made(trigger_id="ms before"), WALL0 - 0.001),
    ]
    created, _ = trigger_records(entries, STARTED, started_wall)
    assert [r.trigger_id for r in created] == ["same ms"]


def test_entries_the_harness_does_not_recognise_are_ignored() -> None:
    entries = [
        Entry(wall=WALL0 + 1, data={"event": "[1, 2]"}),  # JSON, but not an object
        Entry(wall=WALL0 + 2, data={"event": '"trigger_created"'}),
        Entry(wall=WALL0 + 3, data={"event": "{not json"}),
        Entry(wall=WALL0 + 4, data={"event": json.dumps({"event_type": "service_registered"})}),
        Entry(wall=WALL0 + 5, data={"other": "x"}),
    ]
    assert trigger_records(entries, STARTED, WALL0) == ([], [])
    assert notification_records(entries, STARTED, WALL0) == []


@pytest.mark.parametrize(
    "event",
    [
        {"event_type": "trigger_created", "trigger_type": "time", "created_by": "tool-call"},
        {"event_type": "trigger_fired", "trigger_id": "t1", "trigger_type": "time"},
        {**json.loads(made().model_dump_json()), "urgency": "whenever"},
    ],
    ids=["created without a name", "fired without a name", "created with a bad urgency"],
)
def test_an_unreadable_trigger_event_is_a_harness_failure(event: dict[str, object]) -> None:
    """Dropped, it would turn a check's "none created" into a fail; the sample scores E."""
    entries = [Entry(wall=WALL0 + 1, data={"event": json.dumps(event)})]
    with pytest.raises(HarnessError, match=f"unreadable {event['event_type']}"):
        trigger_records(entries, STARTED, WALL0)


def test_an_action_triggers_fire_is_the_engines_action_request_on_alfred_actions() -> None:
    """The engine sends a trigger with an action to alfred:actions as an ActionRequest, in
    place of a TriggerFired. It carries no trigger id, so the newest trigger of this sample
    created before it with that action is the one that fired."""
    plain = TriggerFired(trigger_id="plain", trigger_name="Laundry", trigger_type="time")
    events = [
        event_entry(made(trigger_id="older", action=LAMP), WALL0 + 1),
        event_entry(made(trigger_id="newer", action=LAMP), WALL0 + 2),
        event_entry(made(trigger_id="after the fire", action=LAMP), WALL0 + 7),
        event_entry(made(trigger_id="plain"), WALL0 + 3),
        event_entry(plain, WALL0 + 9),
    ]
    actions = [event_entry(engine_runs(), WALL0 + 5)]
    created, fires = trigger_records(events, STARTED, WALL0, actions=actions)
    assert {r.trigger_id: r.action for r in created}["newer"] == LAMP
    assert {r.trigger_id: r.action for r in created}["plain"] is None
    assert [(f.trigger_id, f.t, f.fired_by, f.urgency) for f in fires] == [
        ("newer", STARTED + 5, "engine", "urgent"),
        ("plain", STARTED + 9, "engine", "informational"),
    ]


def test_an_empty_action_is_no_action_as_the_feature_reads_it() -> None:
    """``create_trigger`` takes a falsy action as none (``core/triggers/feature.py``): the
    trigger fires a TriggerFired, which counts as its fire."""
    fired = TriggerFired(trigger_id="t1", trigger_name="Laundry", trigger_type="time")
    events = [event_entry(made(action={}), WALL0 + 1), event_entry(fired, WALL0 + 2)]
    [record], fires = trigger_records(events, STARTED, WALL0)
    assert record.action is None
    ev = evidence(triggers_created=[record], triggers_fired=fires, step_started=[STARTED])
    result = run_check("trigger_fired", TriggerFiredParams(after_step=0), ev)
    assert result.status == "pass", result.reason


def test_a_created_action_is_kept_as_the_engine_runs_it() -> None:
    """The engine runs the action as an ``ActionPayload``: parameters default to none, and
    a field it does not know is dropped. The record keeps it the same way, so it matches."""
    sent = {"tool_name": "home.scene_turn_on", "target_service": "home-service", "why": "x"}
    events = [event_entry(made(action=sent), WALL0 + 1)]
    actions = [
        event_entry(
            engine_runs({"tool_name": "home.scene_turn_on", "target_service": "home-service"}),
            WALL0 + 2,
        )
    ]
    [record], [fire] = trigger_records(events, STARTED, WALL0, actions=actions)
    assert record.action == {
        "tool_name": "home.scene_turn_on",
        "target_service": "home-service",
        "parameters": {},
    }
    assert fire.trigger_id == "t1"


def test_action_requests_that_are_no_fire_of_this_samples_triggers_are_ignored() -> None:
    events = [event_entry(made(action=LAMP), WALL0 + 1)]
    actions = [
        event_entry(engine_runs(source="conscious-engine"), WALL0 + 2),  # System 2 acting
        event_entry(engine_runs(confirmed=True), WALL0 + 3),  # a confirmed republish
        event_entry(engine_runs(parameters={"target": "light.hall"}), WALL0 + 4),
        event_entry(engine_runs(), WALL0 - 1),  # the previous sample's
        Entry(wall=WALL0 + 5, data={"event": "{not json"}),
        Entry(wall=WALL0 + 6, data={"event": json.dumps({"event_type": "something_else"})}),
    ]
    created, fires = trigger_records(events, STARTED, WALL0, actions=actions)
    assert len(created) == 1 and fires == []


def test_an_unreadable_action_request_is_a_harness_failure() -> None:
    broken = {"event_type": "action_request", "source": "trigger-engine"}  # no tool or service
    actions = [Entry(wall=WALL0 + 1, data={"event": json.dumps(broken)})]
    with pytest.raises(HarnessError, match="unreadable action_request on alfred:actions"):
        trigger_records([], STARTED, WALL0, actions=actions)


def test_is_fire_of_takes_either_kind_of_fire_of_that_trigger() -> None:
    [with_action], _ = trigger_records(
        [event_entry(made(trigger_id="lamp", action=LAMP), WALL0 + 1)], STARTED, WALL0
    )
    [plain], _ = trigger_records([event_entry(made(trigger_id="plain"), WALL0 + 1)], STARTED, WALL0)

    def fired(trigger_id: str) -> Entry:
        event = TriggerFired(trigger_id=trigger_id, trigger_name="x", trigger_type="time")
        return event_entry(event, WALL0 + 2)

    assert is_fire_of(fired("plain"), plain)
    assert not is_fire_of(fired("other"), plain)
    assert is_fire_of(event_entry(engine_runs(), WALL0 + 2), with_action)
    assert not is_fire_of(event_entry(engine_runs(source="reflex-engine"), WALL0 + 2), with_action)
    assert not is_fire_of(event_entry(engine_runs(confirmed=True), WALL0 + 2), with_action)
    assert not is_fire_of(event_entry(engine_runs(), WALL0 + 2), plain)
    assert not is_fire_of(Entry(wall=WALL0 + 2, data={"other": "x"}), plain)


def test_an_unreadable_notification_is_a_harness_failure() -> None:
    with pytest.raises(HarnessError, match="unreadable notification"):
        notification_records(
            [Entry(wall=WALL0 + 1, data={"notification": "{broken"})], STARTED, WALL0
        )
    with pytest.raises(HarnessError, match="unreadable notification"):
        deferred_records([json.dumps({"title": "no urgency", "body": "", "source": "x"})])


def test_notifications_sent_and_held() -> None:
    note = Notification(title="Trigger: Vet", body="At 5", urgency=Urgency.URGENT, source="x")
    sent = notification_records(
        [Entry(wall=WALL0 + 1.5, data={"notification": note.model_dump_json()})],
        STARTED,
        WALL0,
    )
    assert [(n.t, n.title, n.body, n.urgency) for n in sent] == [
        (STARTED + 1.5, "Trigger: Vet", "At 5", "urgent")
    ]
    held = deferred_records([note.model_dump_json()])
    assert [(n.t, n.title) for n in held] == [(None, "Trigger: Vet")]


def test_a_trigger_engine_notification_names_its_trigger() -> None:
    def sent(title: str, source: str) -> str:
        return Notification(
            title=title, body="", urgency=Urgency.URGENT, source=source
        ).model_dump_json()

    raws = [
        sent(f"{TRIGGER_TITLE_PREFIX}Vet", TRIGGER_ENGINE_SOURCE),
        sent(f"{TRIGGER_TITLE_PREFIX}Vet", "conscious-engine"),  # not a trigger's fire
        sent("Vet", TRIGGER_ENGINE_SOURCE),  # no prefix: no trigger to name
    ]
    entries = [Entry(wall=WALL0 + 1 + i, data={"notification": r}) for i, r in enumerate(raws)]
    assert [n.trigger for n in notification_records(entries, STARTED, WALL0)] == [
        "Vet",
        None,
        None,
    ]
    assert [n.trigger for n in deferred_records(raws)] == ["Vet", None, None]


def test_the_triggers_process_registering_mid_sample_is_a_harness_failure() -> None:
    """It registers once, at start, after rehydrating every trigger snapshot: earlier
    samples' deleted triggers are back, and their notifications would read as this one's."""
    other = ServiceRegistered(source="home-service", service_name="home-service")
    assert trigger_records([event_entry(other, WALL0 + 1)], STARTED, WALL0) == ([], [])
    restarted = ServiceRegistered(source=TRIGGER_ENGINE_SOURCE, service_name=TRIGGER_ENGINE_SOURCE)
    with pytest.raises(HarnessError, match="triggers process restarted"):
        trigger_records([event_entry(restarted, WALL0 + 1)], STARTED, WALL0)
    before = trigger_records([event_entry(restarted, WALL0 - 1)], STARTED, WALL0)
    assert before == ([], [])  # the stack's own boot, before the sample


def test_notification_records_are_this_samples_earliest_first() -> None:
    def sent(title: str, wall: float) -> Entry:
        note = Notification(title=title, body="", urgency=Urgency.IMPORTANT, source="x")
        return Entry(wall=wall, data={"notification": note.model_dump_json()})

    records = notification_records(
        [sent("late", WALL0 + 5), sent("early", WALL0 + 2), sent("before", WALL0 - 2)],
        STARTED,
        WALL0,
    )
    assert [(n.title, n.t) for n in records] == [("early", STARTED + 2), ("late", STARTED + 5)]


def prompt(text: str) -> list[dict[str, object]]:
    return [{"role": "user", "content": text}]


def test_local_hour_reads_reflexs_clock_line() -> None:
    assert local_hour(prompt("## Now\nThu 8 Oct, 22:05 (night) · sun down")) == 22
    assert local_hour(prompt("## Now\nMon 12 Jan, 07:59 (morning)")) == 7
    assert local_hour(prompt("It is 22:05.")) is None


def test_tool_domain_takes_the_longest_domain() -> None:
    domains = ["light", "media_player", "media"]
    assert tool_domain("home.light_turn_off", domains) == "light"
    assert tool_domain("home.media_player_media_pause", domains) == "media_player"
    assert tool_domain("home.call_service", domains) is None


def test_targets_resolve_like_home_service_and_add_the_room() -> None:
    world = load_world("apartment")
    assert targets(world, "home.light_turn_off", {"target": "living room"}) == [
        "light.living_room_lamp",
        "living_room",
        "light.living_room_ceiling",
    ]
    assert targets(world, "home.light_turn_on", {"target": "light.bedroom_lamp"}) == [
        "light.bedroom_lamp",
        "bedroom",
    ]
    assert targets(world, "home.light_turn_on", {"target": "Bedroom Lamp"}) == [
        "light.bedroom_lamp",
        "bedroom",
    ]
    assert targets(world, "home.media_player_media_pause", {"target": "Living Room TV"}) == [
        "media_player.living_room_tv",
        "living_room",
    ]
    assert targets(world, "home.light_turn_on", {"target": "Garage"}) == []  # no lights there
    assert targets(world, None, {}) == []


def den(*entities: dict[str, object]) -> World:
    return World.model_validate(
        {
            "name": "den",
            "areas": [{"area_id": "den", "name": "Den"}],
            "entities": list(entities),
            "services": {"light": {"turn_on": {}}},
        }
    )


def test_targets_prefer_a_room_to_a_same_named_entity_and_skip_disabled_ones() -> None:
    world = den(
        {"entity_id": "light.den_lamp", "name": "Den Lamp", "area_id": "den", "state": "on"},
        {
            "entity_id": "light.den_old",
            "name": "Old",
            "area_id": "den",
            "state": "off",
            "disabled": True,
        },
        {"entity_id": "light.reading", "name": "Den", "state": "off"},
        {"entity_id": "light.spare", "name": "Spare", "state": "off", "disabled": True},
    )
    assert targets(world, "home.light_turn_on", {"target": "Den"}) == ["light.den_lamp", "den"]
    assert targets(world, "home.light_turn_on", {"target": "Spare"}) == []
    assert targets(world, "home.light_turn_on", {"target": "light.spare"}) == []


def test_targets_match_the_friendly_name_home_service_sees() -> None:
    """home-service reads the state's ``friendly_name``, which a world entity's attributes
    may set over its name."""
    world = den(
        {
            "entity_id": "light.reading",
            "name": "Reading",
            "state": "off",
            "attributes": {"friendly_name": "Den Reading Light"},
        },
    )
    assert targets(world, "home.light_turn_on", {"target": "Den Reading Light"}) == [
        "light.reading"
    ]
    assert targets(world, "home.light_turn_on", {"target": "Reading"}) == []


REFLEX_TOOL = ToolInfo(
    name="home.light_turn_off",
    description="",
    parameters={"target": {}},
    feature_name="home",
    feature_description="",
    target_service="home-service",
    audience="reflex",
)


def s1(text: str | None, status: int = 200, role: str = "system1", t: float = 3.0) -> LlmCall:
    return LlmCall(
        t=t,
        role=role,  # type: ignore[arg-type]
        latency_ms=700.0,
        status=status,
        messages=prompt("Thu 8 Oct, 22:05 (night)"),
        response_text=text,
    )


def test_a_reflex_calls_done_is_when_the_proxy_answered() -> None:
    queued = s1(json.dumps({"decision": "none"})).model_copy(update={"answered_at": 4.2})
    [call] = reflex_calls([queued], [REFLEX_TOOL], load_world("apartment"))
    assert call.done == 4.2


def test_reflex_calls_parse_system1_replies_the_way_reflex_does() -> None:
    world = load_world("apartment")
    act = json.dumps(
        {
            "decision": "act",
            "tool_name": "home.light_turn_off",
            "parameters": {"target": "Living Room"},
            "reason": "the TV started at night",
        }
    )
    calls = reflex_calls(
        [
            s1(act),
            s1(json.dumps({"decision": "none", "reason": "nothing to do"})),
            s1(json.dumps({"decision": "act", "tool_name": "home.lock_unlock"})),
            s1(None, status=502),
            s1(act, role="system2"),
        ],
        [REFLEX_TOOL],
        world,
    )
    assert [c.decision for c in calls] == ["act", "none", "invalid", "invalid"]
    first = calls[0]
    assert first.tool == "home.light_turn_off" and "living_room" in first.targets
    assert first.local_hour == 22 and first.reason == "the TV started at night"
    assert first.done == 3.7
    assert "not one of Reflex's tools" in (calls[2].problem or "")
    assert calls[3].problem == "no reply (HTTP 502)"


def test_reflex_calls_come_back_in_request_order() -> None:
    """The proxy records a call when its reply returns, so a slow call can be recorded
    after a quicker one sent later. Checks take ``[0]`` as the earliest request."""
    none = json.dumps({"decision": "none"})
    calls = reflex_calls(
        [s1(none, t=5.0), s1(none, t=4.0), s1(None, status=500, t=4.5)],
        [REFLEX_TOOL],
        load_world("apartment"),
    )
    assert [c.t for c in calls] == [4.0, 4.5, 5.0]
