"""A deterministic timeline of a lived-in house, built from ``scenario.py``.

Same seed, same timeline — down to every id, timestamp and attribute — so two runs
of the eval write byte-identical memories in the same order.
"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel

from bus.schemas.events import ActionRequest, ActionResult, ReflexObservation
from core.memory.schemas import RoutineSpec, RoutineStep
from evals.memory.scenario import (
    DETAIL_OBSERVATIONS,
    EVENING,
    HOME_ACTIVITY,
    RECALLED_MEMORIES,
    REFLEX_TEMPLATES,
    ROUTINES,
    SEMANTIC_FILES,
    SIGNIFICANT_EVENTS,
    SPEAKER_PLAYLISTS,
    THERMOSTAT_SETPOINTS,
    TOGGLE_DEVICES,
    TV_TITLES,
)

# A Monday, so day 0 is a weekday.
SIM_START = datetime(2026, 1, 5, tzinfo=UTC)
DEFAULT_DAYS = 60
DEFAULT_SEED = 7
# When sir asks about a recalled memory (hour, minute of the day).
RETRIEVAL_TIME = (19, 30)
_SOURCE = "reflex-engine"


class Category(StrEnum):
    """What a memory is, from the user's point of view."""

    ROUTINE = "routine"  # a passive state change nobody acted on
    REFLEX = "reflex"  # a System 1 action
    DETAIL = "detail"  # a one-off, low-significance observation worth asking about
    SIGNIFICANT = "significant"  # an alert, something sir said, an appointment
    RECALLED = "recalled"  # something sir keeps coming back to


# The "noise" decay exists to clear out.
NOISE: frozenset[Category] = frozenset({Category.ROUTINE, Category.REFLEX})


class SimMemory(BaseModel):
    """One episodic memory to write at ``at``.

    With ``observation`` set it goes through the Memory Ingestor
    (``ingest_observation``); otherwise it is an ``EpisodicEntry`` scored by the
    heuristic ``SignificanceScorer``, as the Librarian writes it without an LLM.
    """

    id: str
    at: datetime
    category: Category
    observation: ReflexObservation | None = None
    source: str = ""
    summary: str = ""
    entities: list[str] = []


class Probe(BaseModel):
    """A question whose answer is one known memory."""

    question: str
    target_id: str
    category: Category


class Retrieval(BaseModel):
    """Sir deliberately recalls ``target_id`` at ``at``."""

    at: datetime
    target_id: str


class Dataset(BaseModel):
    seed: int
    days: int
    start: datetime
    memories: list[SimMemory]
    probes: list[Probe]
    retrievals: list[Retrieval]
    semantic_files: dict[str, str]
    routines: list[RoutineSpec]

    def category_of(self) -> dict[str, Category]:
        return {m.id: m.category for m in self.memories}


def build_dataset(seed: int = DEFAULT_SEED, days: int = DEFAULT_DAYS) -> Dataset:
    """Build ``days`` days of house activity. Scenario content past ``days`` is dropped."""
    rng = random.Random(seed)
    end = SIM_START + timedelta(days=days)
    memories: list[SimMemory] = []
    for day in range(days):
        memories.extend(_routine_day(rng, day, end))
    probes: list[Probe] = []
    retrievals: list[Retrieval] = []

    for index, event in enumerate(SIGNIFICANT_EVENTS):
        if event.day >= days:
            continue
        memory = SimMemory(
            id=f"sig-{index:02d}",
            at=_at(event.day, event.hour, event.minute),
            category=Category.SIGNIFICANT,
            source=event.source,
            summary=event.summary,
            entities=list(event.entities),
        )
        memories.append(memory)
        if event.probe:
            probes.append(
                Probe(question=event.probe, target_id=memory.id, category=memory.category)
            )

    for index, recalled in enumerate(RECALLED_MEMORIES):
        if recalled.day >= days:
            continue
        memory_id = f"rec-{index:02d}"
        at = _at(recalled.day, recalled.hour, recalled.minute)
        if recalled.source == "observation":
            assert recalled.transition is not None
            entity, old, new, attributes = recalled.transition
            memory = SimMemory(
                id=memory_id,
                at=at,
                category=Category.RECALLED,
                observation=_passive(memory_id, at, entity, old, new, dict(attributes)),
            )
        else:
            memory = SimMemory(
                id=memory_id,
                at=at,
                category=Category.RECALLED,
                source=recalled.source,
                summary=recalled.summary,
                entities=list(recalled.entities),
            )
        memories.append(memory)
        probes.append(Probe(question=recalled.probe, target_id=memory_id, category=memory.category))
        retrieval_day = recalled.day + 1
        while retrieval_day < days:
            retrievals.append(
                Retrieval(at=_at(retrieval_day, *RETRIEVAL_TIME), target_id=memory_id)
            )
            retrieval_day += recalled.every_days

    for index, detail in enumerate(DETAIL_OBSERVATIONS):
        if detail.day >= days:
            continue
        memory_id = f"det-{index:02d}"
        at = _at(detail.day, detail.hour, detail.minute)
        memories.append(
            SimMemory(
                id=memory_id,
                at=at,
                category=Category.DETAIL,
                observation=_passive(
                    memory_id, at, detail.entity_id, detail.old, detail.new, dict(detail.attributes)
                ),
            )
        )
        probes.append(Probe(question=detail.probe, target_id=memory_id, category=Category.DETAIL))

    memories.sort(key=lambda m: (m.at, m.id))
    retrievals.sort(key=lambda r: (r.at, r.target_id))
    routines = [
        RoutineSpec(
            name=seed_routine.name,
            trigger_pattern=seed_routine.trigger_pattern,
            steps=[RoutineStep(description=step) for step in seed_routine.steps],
            confidence=seed_routine.confidence,
            learned_from=list(seed_routine.learned_from),
            state=seed_routine.state,
        )
        for seed_routine in ROUTINES
    ]
    return Dataset(
        seed=seed,
        days=days,
        start=SIM_START,
        memories=memories,
        probes=probes,
        retrievals=retrievals,
        semantic_files=dict(SEMANTIC_FILES),
        routines=routines,
    )


# ---------------------------------------------------------------------------
# One day of routine activity
# ---------------------------------------------------------------------------


def _at(day: int, hour: int, minute: int, second: int = 0) -> datetime:
    return SIM_START + timedelta(days=day, hours=hour, minutes=minute, seconds=second)


def _time_in(rng: random.Random, day: int, profile: tuple[float, ...]) -> datetime:
    hour = rng.choices(range(24), weights=profile)[0]
    return _at(day, hour, rng.randrange(60), rng.randrange(60))


def _passive(
    memory_id: str,
    at: datetime,
    entity_id: str,
    old: str,
    new: str,
    attributes: dict[str, str | int],
) -> ReflexObservation:
    """A state change the Reflex Engine saw and did not act on."""
    return ReflexObservation(
        event_id=f"{memory_id}:event",
        observation_id=memory_id,
        timestamp=at,
        source=_SOURCE,
        origin="state_change",
        trigger_event={
            "entity_id": entity_id,
            "old_state": old,
            "new_state": new,
            "attributes": attributes,
        },
    )


# (entity, old, new, attributes, at) — ids are assigned once the day is sorted.
type _Change = tuple[str, str, str, dict[str, str | int], datetime]


def _routine_day(rng: random.Random, day: int, end: datetime) -> list[SimMemory]:
    weekday = (SIM_START + timedelta(days=day)).weekday() < 5
    changes: list[_Change] = []

    for device in TOGGLE_DEVICES:
        low, high = (
            device.per_day
            if weekday or device.weekend_per_day is None
            else (device.weekend_per_day)
        )
        for _ in range(rng.randint(low, high)):
            at = _time_in(rng, day, device.hours or HOME_ACTIVITY)
            on_attributes: dict[str, str | int] = {"friendly_name": device.friendly_name}
            if device.brightness:
                on_attributes = {"brightness": rng.choice(device.brightness), **on_attributes}
            changes.append((device.entity_id, device.idle, device.active, on_attributes, at))
            back = at + timedelta(
                minutes=rng.randint(*device.hold_minutes), seconds=rng.randrange(60)
            )
            changes.append(
                (
                    device.entity_id,
                    device.active,
                    device.idle,
                    {"friendly_name": device.friendly_name},
                    back,
                )
            )

    setpoint = rng.choice(THERMOSTAT_SETPOINTS)
    for _ in range(rng.randint(3, 5)):
        setpoint = rng.choice([s for s in THERMOSTAT_SETPOINTS if s != setpoint])
        changes.append(
            (
                "climate.thermostat",
                "heat",
                "heat",
                {"temperature": setpoint, "friendly_name": "Thermostat"},
                _time_in(rng, day, HOME_ACTIVITY),
            )
        )

    reading = _outdoor_temperature(day, 0, rng)
    for hour in range(24):
        new_reading = _outdoor_temperature(day, hour, rng)
        changes.append(
            (
                "sensor.outdoor_temperature",
                f"{reading:.1f}",
                f"{new_reading:.1f}",
                {"friendly_name": "Outdoor Temperature"},
                _at(day, hour, rng.randrange(60), rng.randrange(60)),
            )
        )
        reading = new_reading

    tv: dict[str, str | int] = {"friendly_name": "Living Room TV"}
    for _ in range(rng.randint(1, 2)):
        title = rng.choice(TV_TITLES)
        playing: dict[str, str | int] = {"media_title": title, **tv}
        start = _time_in(rng, day, EVENING)
        paused = start + timedelta(minutes=rng.randint(20, 50))
        resumed = paused + timedelta(minutes=rng.randint(2, 10))
        stopped = resumed + timedelta(minutes=rng.randint(20, 60))
        changes += [
            ("media_player.living_room_tv", "off", "playing", playing, start),
            ("media_player.living_room_tv", "playing", "paused", playing, paused),
            ("media_player.living_room_tv", "paused", "playing", playing, resumed),
            ("media_player.living_room_tv", "playing", "off", dict(tv), stopped),
        ]

    speaker = {"friendly_name": "Kitchen Speaker"}
    for _ in range(rng.randint(0, 2)):
        start = _time_in(rng, day, HOME_ACTIVITY)
        title = rng.choice(SPEAKER_PLAYLISTS)
        changes += [
            (
                "media_player.kitchen_speaker",
                "idle",
                "playing",
                {"media_title": title, **speaker},
                start,
            ),
            (
                "media_player.kitchen_speaker",
                "playing",
                "idle",
                dict(speaker),
                start + timedelta(minutes=rng.randint(20, 90)),
            ),
        ]

    if day % 2 == 0:
        vacuum = {"friendly_name": "Robot Vacuum"}
        start = _at(day, 13, rng.randrange(60))
        returning = start + timedelta(minutes=rng.randint(40, 70))
        changes += [
            ("vacuum.robot", "docked", "cleaning", dict(vacuum), start),
            ("vacuum.robot", "cleaning", "returning", dict(vacuum), returning),
            (
                "vacuum.robot",
                "returning",
                "docked",
                dict(vacuum),
                returning + timedelta(minutes=rng.randint(3, 8)),
            ),
        ]

    if weekday:
        person = {"friendly_name": "Sir"}
        changes += [
            ("person.sir", "home", "not_home", dict(person), _at(day, 8, rng.randint(0, 40))),
            ("person.sir", "not_home", "home", dict(person), _at(day, 17, rng.randint(30, 89))),
        ]

    sun = {"friendly_name": "Sun"}
    changes += [
        ("sun.sun", "below_horizon", "above_horizon", dict(sun), _at(day, 7, 40 - day // 3)),
        ("sun.sun", "above_horizon", "below_horizon", dict(sun), _at(day, 16, 45 + day // 3)),
    ]

    passive = sorted((c for c in changes if c[4] < end), key=lambda c: (c[4], c[0]))
    memories = [
        SimMemory(
            id=f"obs-{day:02d}-{index:04d}",
            at=at,
            category=Category.ROUTINE,
            observation=_passive(f"obs-{day:02d}-{index:04d}", at, entity, old, new, attributes),
        )
        for index, (entity, old, new, attributes, at) in enumerate(passive)
    ]
    memories.extend(_reflex_day(rng, day, end))
    return memories


def _outdoor_temperature(day: int, hour: int, rng: random.Random) -> float:
    """A winter day: coldest before dawn, warmest mid-afternoon, drifting warmer."""
    daily = 4.0 * math.sin((hour - 9) / 24 * 2 * math.pi)
    return 3.0 + day * 0.08 + daily + rng.uniform(-0.6, 0.6)


def _reflex_day(rng: random.Random, day: int, end: datetime) -> list[SimMemory]:
    actions: list[tuple[datetime, int]] = []
    for template_index, template in enumerate(REFLEX_TEMPLATES):
        for _ in range(rng.randint(*template.per_day)):
            actions.append((_time_in(rng, day, template.hours), template_index))
    actions.sort()

    memories: list[SimMemory] = []
    for index, (at, template_index) in enumerate(a for a in actions if a[0] < end):
        template = REFLEX_TEMPLATES[template_index]
        memory_id = f"rfx-{day:02d}-{index:03d}"
        request = ActionRequest(
            event_id=f"{memory_id}:request",
            request_id=f"{memory_id}:request",
            timestamp=at,
            source=_SOURCE,
            target_service="home-service",
            tool_name=template.tool_name,
            parameters=dict(template.parameters),
        )
        memories.append(
            SimMemory(
                id=memory_id,
                at=at,
                category=Category.REFLEX,
                observation=ReflexObservation(
                    event_id=f"{memory_id}:event",
                    observation_id=memory_id,
                    timestamp=at,
                    source=_SOURCE,
                    origin="state_change",
                    trigger_event={
                        "entity_id": template.trigger_entity,
                        "old_state": template.trigger_old,
                        "new_state": template.trigger_new,
                        "attributes": {},
                    },
                    action=request,
                    result=ActionResult(
                        event_id=f"{memory_id}:result",
                        request_id=request.request_id,
                        timestamp=at,
                        source="home-service",
                        tool_name=template.tool_name,
                        status="success",
                    ),
                    decision_context=template.reason,
                ),
            )
        )
    return memories
