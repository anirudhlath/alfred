# PRD Eval Suite — Slice 2 (Reflex, Triggers, Notifications) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `alfred evals run reflex triggers notifications` pushes house events, reminders and do-not-disturb through the real stack. It scores three things: Reflex's shadow decisions, the triggers Alfred creates and fires, and the notifications it sends or defers. The reflex suite reports a pass rate per golden over several epochs (#298).

**Architecture:** The driver gets a `ContainerBus`, which is the stack's Redis plus an admin-API client.

- **Reads into `Evidence`:**
  - `alfred:events`, for triggers created and fired;
  - the notification dispatch stream;
  - the deferred list;
  - the reflex-audience tools.
- **Acts:**
  - pulls a trigger's `run_at` to now;
  - sets do-not-disturb (DND) through the admin API;
  - sets the stored user timezone. That timezone is the clock Reflex's prompt shows.
- **System 1's replies:** the LLM proxy already records them. They are parsed with Reflex's own `parse_decision`, against the tools Reflex's prompt showed.
- **Checks** stay pure functions of `Evidence`.
- **Cleanup:** after each sample the driver removes the triggers it created, turns DND off and restores the clock, so samples sharing a container stay independent.

**Tech Stack:**
- Python 3.13
- Inspect AI 0.3.277
- redis.asyncio
- httpx
- Pydantic v2
- pytest and pytest-asyncio (`asyncio_mode = "auto"`)
- fakeredis (new, dev only)

**Spec:** `docs/superpowers/specs/2026-10-06-prd-eval-suite-design.md`.

Also in scope:
- the goldens table in `docs/superpowers/specs/2026-10-07-reflex-context-design.md` ("Goldens for the PRD suite's `reflex` suite");
- issue #298, whose acceptance criteria this plan meets.

## Decisions this plan makes

The owner should see these before approving. Each one departs from the spec's letter, or settles something the spec left open.

1. **Trigger evidence comes from `alfred:events`, not `GET /triggers`.**
   - `create_trigger` writes `TriggerCreated` there, and the engine writes `TriggerFired`.
   - A one-shot trigger is deleted from `alfred:triggers` the moment it fires, so a list read after the sample would miss it.
   - The only reachable list route is `/api/admin/triggers`, and it needs a session.
2. **Reflex's decision is read from System 1's recorded reply, not from `alfred:reflex:observations`.**
   - A `none` decision is never published there with its proposal: it is debounced, or skipped for trigger events.
   - The reply is parsed by `core/reflex/decision.parse_decision` with the reflex-audience tools from the container's registry, the same list Reflex's prompt shows.
   - This is what #298 asks for.
3. **The time of day comes from the stored user timezone (`alfred:user:timezone`).**
   - Reflex's clock is the container's UTC time shown in that zone (`core/reflex/prompt.render_now`). A `clock: {hour: 22}` step picks the `Etc/GMT±N` zone whose local hour is 22 right now.
   - The resolution is one hour, which is all Reflex's time-of-day label uses. The step never runs in the last two minutes of an hour.
   - The reflex checks confirm the prompt really showed that hour. If it did not, the check reports an error, not a failure.
4. **`prompt_not_contains` is pulled forward from slice 3.** The reflex spec's `prompt_is_compact` golden needs it.
5. **Coverage moves:**
   - `4.3.s2-observes-s1` maps to `memory` only. System 2 sees System 1 through episodic memory (`core/memory/ingestor.py`), which memory checks (slice 4) can read.
   - `7.proactivity-quality` maps to `memory` only. It measures accepted versus dismissed routine suggestions, which is the librarian's job.
   - Both rows stay `pending_suites`.
6. **`4.4.tiered-autonomy` goldens are `status: pending`.** The PRD row is Planned.
7. **`notification` has no `channel` parameter.**
   - `Notification` carries no channel; the delivery workers route by urgency (`core/notifications/delivery.py`).
   - The suite checks what reaches the dispatch stream or the deferred list.
   - `4.2.notification-delivery` also lists `tests/core/notifications` for the adapters themselves.
8. **Every sample cleans up after itself, targeted rather than `FLUSHALL`:**
   - it deletes the triggers it created;
   - it clears DND and the deferred list if it touched DND;
   - it puts the user timezone back if the sample changed it. A `clock` step changes it, and so does every user step: System 2 stores the request's timezone, and the default actor's is `America/Denver`.
9. **Reflex latency (`reflex_ms`) runs from the `ha_event` push until System 1's reply comes back.** In shadow mode the decision is the last thing Reflex does. The PRD's "Event → action < 500 ms" is held to that.
10. **The reflex suite's PR runs twice with `--epochs 5`.** That makes the run-to-run spread visible, as #298 requires. vLLM is not deterministic at temperature 0: PR #296 measured it.

## Global Constraints

- **CLI.** `alfred evals <command>`, never `alfred-evals`.
- **Python and checks.**
  - Python 3.13+, Pydantic v2, async-first.
  - `mypy --strict` covers `alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/ alfred_cli/`.
  - ruff, line length 100, with rules `B` and `TCH`:
    - annotation-only imports go under `TYPE_CHECKING` unless Pydantic needs them at runtime;
    - mark the runtime ones `# noqa: TC00x — Pydantic resolves …`, as `evals/harness/checks/llm.py` does.
  - Run `.venv/bin/ruff check --fix` and `.venv/bin/ruff format` on the touched files before every commit.
  - Run tests with `.venv/bin/python -m pytest`.
- **Dependencies.** `fakeredis` joins the `dev` optional extra (`uv add --optional dev fakeredis`). Nothing else is new.
- **Model in every role:** vLLM `gemma-4-26b-a4b` at `http://localhost:8000/v1`. Embeddings: `BAAI/bge-m3` at `http://localhost:8001`.
- **Load on the shared vLLM.** The proxy holds at most 2 upstream requests and the judge at most 2. Only one eval container runs at a time.
- **No real home.** The world is the fictional apartment. Its people are **Alex** and **Sam**. No real names, addresses, IPs or tokens appear in any committed file under `evals/`.
- **Never `FLUSHALL`.** A fresh container is the full reset. Per-sample cleanup deletes only what the sample made.
- **Default waits:**

  | Wait | Default |
  |---|---|
  | System 1 after an `ha_event` a reflex check watches | 15 s |
  | Reflex's attention cooldown after a restore | 6 s |
  | A trigger's fire after `advance_trigger` | 15 s |
  | Settle after a `dnd` step | 3 s |
  | Bus poll interval | 0.2 s |

- **Defaults kept from slice 1:** 3 epochs, 120 s reply timeout, 420 s boot timeout.
- **Score values:**
  - `C`: every counted check passed
  - `I`: a counted check failed
  - `N`: inconclusive
  - `E`: the harness failed
  
  A check that cannot trust its own evidence (for example, the clock did not take) reports `error`, never `fail`.
- **Commits.** Conventional messages. Never a model identifier in a commit or PR.
- **The repo is public.** Never quote the secret-hygiene grep literals in a committed file.

## Review Focus

1. **The clock step lands in the last minute of an hour, or the container has no `Etc/GMT` zones.** Reflex would read the next hour, or UTC, and score a golden against the wrong time of day.
   - The driver waits out minutes 58–59 before setting the clock (Task 9, `test_clock_waits_out_the_last_minutes_of_an_hour`).
   - The reflex checks error when the prompt's hour differs from the clock step's (Task 3, `test_reflex_check_errors_when_the_clock_did_not_take`).
2. **The restore, or the previous sample's last event, touched the entity the golden is about to change.** Reflex ignores an entity for 5 s after it fires (`core/reflex/attention.py`), so the golden's own event would be swallowed.
   - A reflex golden waits 6 s before its first step, even when nothing needed restoring (Task 9, `test_reflex_golden_waits_out_the_attention_cooldown_after_a_restore`).
   - A golden that changes one entity twice puts a `wait: 6` between the two changes (Task 11).
3. **A sample's triggers, DND or clock leak into the next sample in the same container.** For example, a sensor trigger from epoch 1 fires during epoch 2.
   - Cleanup removes all three (Task 9, `test_play_cleans_up_triggers_dnd_and_clock`).
   - `trigger_fired` counts only triggers created in the sample (Task 4, `test_trigger_fired_ignores_triggers_from_other_samples`).
4. **Alfred never creates the trigger that an `advance_trigger` step wants.** That is Alfred's failure, so the sample must score `I`, not `E`. The step notes it in the transcript and moves on (Task 9, `test_advance_without_a_trigger_is_alfreds_failure_not_the_harness`).
5. **The admin API refuses the DND call, or Redis drops mid-sample.** That is the harness's failure: the sample scores `E` and the stack is marked dirty (Task 10, `test_a_bus_failure_errors_the_sample_and_dirties_the_stack`).

---

## File Structure

```
evals/harness/checks/matching.py       # + nested mappings, {regex}, validate_expected
evals/harness/checks/reply.py          # Needles base extracted from ReplyTextParams
evals/harness/checks/llm.py            # + prompt_not_contains, message_text
evals/harness/checks/reflex.py         # NEW: reflex_decision, reflex_not_proposed
evals/harness/checks/triggers.py       # NEW: trigger_created, trigger_not_created, trigger_fired
evals/harness/checks/notifications.py  # NEW: notification
evals/harness/checks/latency.py        # + reflex_ms, reminder_fire_ms, at_step
evals/harness/checks/__init__.py       # registry; needs_reply, step_kind_needed, watches_reflex
evals/harness/evidence.py              # + ReflexCall, TriggerRecord, TriggerFire,
                                       #   NotificationRecord, Advance, ClockSet; step helpers
evals/harness/scenario.py              # + clock, advance_trigger, dnd steps; coherence
evals/harness/world.py                 # + World.area_of, World.entities_in
evals/harness/worlds/apartment.yaml    # + people, sun, a button, two prompt-noise attributes
evals/harness/bus.py                   # NEW: ContainerBus, Bus protocol, zone_for_hour
evals/harness/collect.py               # NEW: bus entries → evidence records
evals/harness/reflex.py                # NEW: System 1 calls → ReflexCall
evals/harness/driver.py                # new steps, reflex wait, collection, cleanup
evals/harness/stack.py                 # Stack.bus
evals/harness/tasks.py                 # a bus failure dirties the stack
evals/harness/orchestrate.py           # PlayContext(bus=stack.bus)
evals/suites/reflex/*.yaml             # NEW goldens
evals/suites/triggers/*.yaml           # NEW goldens
evals/suites/notifications/*.yaml      # NEW goldens
evals/coverage.yaml
tests/evals/harness/factories.py       # evidence(**extra), FakeBus
tests/evals/harness/test_checks.py     # matching, prompt_not_contains
tests/evals/harness/test_checks_reflex.py         # NEW
tests/evals/harness/test_checks_triggers.py       # NEW (triggers, notifications, latency)
tests/evals/harness/test_evidence.py              # NEW
tests/evals/harness/test_scenario.py
tests/evals/harness/test_world.py
tests/evals/harness/test_bus.py                   # NEW
tests/evals/harness/test_collect.py               # NEW (collect + reflex parsing)
tests/evals/harness/test_driver.py
tests/evals/harness/test_stack.py
tests/evals/harness/test_tasks.py
tests/evals/test_goldens_load.py
docs/evals.md
pyproject.toml, uv.lock
```

---

### Task 1: Matching — nested mappings and `{regex}`

The trigger checks compare a trigger's normalised `conditions` mapping. Two examples:

- `{cron: "0 7 * * 1-5"}`, which a model may also spell `MON-FRI`;
- the `conditions` argument System 2 passes to `triggers.create_trigger`.

Matching needs two new expected forms:

- a mapping (each key present, each value matching);
- `{regex: p}`.

A bad regex must fail at load.

**Files:**
- Modify: `evals/harness/checks/matching.py`
- Modify: `evals/harness/checks/llm.py` (`ToolArgsParams`)
- Test: `tests/evals/harness/test_checks.py`

**Interfaces:**
- Produces:
  - `value_matches(expected, actual) -> bool`, which now takes mappings and `{regex}`;
  - `is_approx(expected) -> bool` (renamed from `_is_approx`);
  - `is_regex(expected) -> bool`;
  - `validate_expected(expected) -> None`, which raises `ValueError`.

- [ ] **Step 1: Write the failing tests** (append to `tests/evals/harness/test_checks.py`)

```python
from evals.harness.checks.llm import ToolArgsParams
from evals.harness.checks.matching import validate_expected, value_matches

CRON = r"0 7 \* \* (1-5|mon-fri)"


@pytest.mark.parametrize(
    ("expected", "actual", "ok"),
    [
        ({"run_in_seconds": {"approx": 1200, "tol": 60}}, {"run_in_seconds": 1230, "x": 1}, True),
        ({"run_in_seconds": {"approx": 1200, "tol": 60}}, {"run_in_seconds": 1300}, False),
        ({"entity_id": "binary_sensor.front_door"}, {}, False),
        ({"a": {"b": "on"}}, {"a": {"b": "ON "}}, True),
        ({"a": 1}, "a=1", False),
        ({}, {"anything": 1}, True),
        ({"regex": CRON}, "0 7 * * MON-FRI", True),
        ({"regex": CRON}, " 0 7 * * 1-5 ", True),
        ({"regex": CRON}, "30 7 * * 1-5", False),  # the whole string must match
        ({"regex": "on"}, 1, False),
        ({"cron": {"regex": CRON}}, {"cron": "0 7 * * 1-5"}, True),
    ],
)
def test_value_matches_mappings_and_regex(expected: object, actual: object, ok: bool) -> None:
    assert value_matches(expected, actual) is ok


def test_validate_expected_rejects_a_regex_that_does_not_compile_anywhere() -> None:
    validate_expected({"a": [{"regex": "x+"}], "b": {"approx": 1, "tol": 1}})
    with pytest.raises(ValueError, match="does not compile"):
        validate_expected({"conditions": {"cron": {"regex": "0 7 ("}}})
    with pytest.raises(ValueError, match="must be a string"):
        validate_expected([{"regex": 7}])


def test_tool_args_with_a_bad_regex_fail_at_load() -> None:
    with pytest.raises(ValidationError, match="does not compile"):
        ToolArgsParams(tool="triggers.create_trigger", args={"conditions": {"cron": {"regex": "("}}})
```

(Add `from pydantic import ValidationError` to the file's imports if it is not there already.)

- [ ] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_checks.py -k "mappings_and_regex or validate_expected or bad_regex" -q`
Expected: FAIL. The import error is `cannot import name 'validate_expected'`.

- [ ] **Step 3: Implement**

Replace the docstring and the helpers in `evals/harness/checks/matching.py`, and add the two new branches to `value_matches`:

```python
"""Expected-vs-actual matching shared by the check families.

An expected value is one of:

- a plain scalar; strings compare case-insensitively;
- a list: each element must match some actual element;
- a mapping: each key must be present with a matching value, and other keys are ignored;
- ``{approx: x, tol: t}``, for numbers;
- ``{regex: p}``, for strings: the whole trimmed string must match, case-insensitively.
"""

from __future__ import annotations

import json
import re
from typing import Any


def as_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def is_approx(expected: object) -> bool:
    return (
        isinstance(expected, dict) and "approx" in expected and set(expected) <= {"approx", "tol"}
    )


def is_regex(expected: object) -> bool:
    return isinstance(expected, dict) and set(expected) == {"regex"}


def validate_expected(expected: object) -> None:
    """Raise ValueError for a ``{regex}`` anywhere in *expected* that is not a compiling
    string, so a golden with one fails at load instead of erroring every sample."""
    if is_regex(expected):
        assert isinstance(expected, dict)
        pattern = expected["regex"]
        if not isinstance(pattern, str):
            raise ValueError(f"regex must be a string, got {pattern!r}")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"regex {pattern!r} does not compile: {exc}") from exc
    elif isinstance(expected, dict):
        for value in expected.values():
            validate_expected(value)
    elif isinstance(expected, list):
        for value in expected:
            validate_expected(value)


def value_matches(expected: object, actual: object) -> bool:
    if is_approx(expected):
        assert isinstance(expected, dict)
        number = as_number(actual)
        target = as_number(expected["approx"])
        tol = as_number(expected.get("tol", 0)) or 0.0
        return number is not None and target is not None and abs(number - target) <= tol
    if is_regex(expected):
        assert isinstance(expected, dict)
        return isinstance(actual, str) and (
            re.fullmatch(expected["regex"], actual.strip(), re.IGNORECASE) is not None
        )
    if isinstance(expected, bool):
        if isinstance(actual, str):
            return actual.strip().lower() == str(expected).lower()
        return actual is expected
    if isinstance(expected, int | float):
        number = as_number(actual)
        return number is not None and number == float(expected)
    if isinstance(expected, str):
        return isinstance(actual, str) and actual.strip().lower() == expected.strip().lower()
    if isinstance(expected, list):
        return isinstance(actual, list) and all(
            any(value_matches(e, a) for a in actual) for e in expected
        )
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and value_matches(value, actual[key]) for key, value in expected.items()
        )
    return expected == actual
```

Keep `describe()` unchanged. Then run `grep -rn "_is_approx" evals tests` and rename every hit to `is_approx`.

In `evals/harness/checks/llm.py`, give `ToolArgsParams` a validator. Add `field_validator` to the pydantic import and `validate_expected` to the matching import:

```python
class ToolArgsParams(ToolParams):
    args: dict[str, Any]

    @field_validator("args")
    @classmethod
    def _patterns_compile(cls, value: dict[str, Any]) -> dict[str, Any]:
        validate_expected(value)
        return value
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_checks.py -q`
Expected: PASS. Every test in the file passes, including slice 1's matching tests.

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff check --fix evals/harness/checks tests/evals/harness/test_checks.py
.venv/bin/ruff format evals/harness/checks tests/evals/harness/test_checks.py
git add evals/harness/checks/matching.py evals/harness/checks/llm.py tests/evals/harness/test_checks.py
git commit -m "feat(evals): match nested mappings and {regex} in expected values"
```

---

### Task 2: Evidence for Reflex, triggers, notifications and the new steps

**Files:**
- Modify: `evals/harness/evidence.py`
- Modify: `tests/evals/harness/factories.py`
- Test: `tests/evals/harness/test_evidence.py` (new)

**Interfaces:**
- Produces, in `evals/harness/evidence.py`:
  - `Decision = Literal["act", "ask", "none", "invalid"]`
  - `Urgency = Literal["informational", "important", "urgent"]`
  - `StepKind = Literal["user", "ha_event", "wait", "clock", "advance_trigger", "dnd"]` and `STEP_KINDS: tuple[StepKind, ...]`, in that order
  - `ReflexCall(t, latency_ms, decision, reason="", tool=None, parameters={}, targets=[], problem=None, local_hour=None)`, with the property `done -> float`
  - `TriggerRecord(t, trigger_id, trigger_type, name, created_by, conditions={}, urgency="informational", one_shot=False, created_at: datetime)`
  - `TriggerFire(t, trigger_id, name, trigger_type, urgency, fired_by)`
  - `NotificationRecord(t: float | None, title, body="", urgency, source)`
  - `Advance(step, trigger_id, name, t)`
  - `ClockSet(step, hour, tz)`
  - New `Evidence` fields, all defaulting to empty: `step_kinds`, `reflex`, `triggers_created`, `triggers_fired`, `notifications`, `deferred`, `advances`, `clocks`
  - New `Evidence` methods:
    - `step_index(step) -> int`, which raises `IndexError`
    - `step_window(step) -> tuple[float, float]`
    - `last_step(kind) -> int | None`
    - `clock_at(step) -> ClockSet | None`
- Produces, in `tests/evals/harness/factories.py`: `evidence(..., **extra)`, which passes `extra` through to `Evidence`.

- [ ] **Step 1: Write the failing tests** (`tests/evals/harness/test_evidence.py`)

```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from evals.harness.evidence import (
    STEP_KINDS,
    Advance,
    ClockSet,
    Evidence,
    NotificationRecord,
    ReflexCall,
    TriggerFire,
    TriggerRecord,
)
from tests.evals.harness.factories import evidence


def three_steps() -> Evidence:
    return evidence(
        step_started=[0.0, 10.0, 20.0],
        step_kinds=["clock", "ha_event", "ha_event"],
        clocks=[ClockSet(step=0, hour=22, tz="Etc/GMT-7")],
    )


def test_step_window_runs_to_the_next_step_and_the_last_to_the_end() -> None:
    ev = three_steps()
    assert ev.step_window(0) == (0.0, 10.0)
    assert ev.step_window(1) == (10.0, 20.0)
    assert ev.step_window(-1) == (20.0, 100.0)  # ended_at is 100 in the factory


def test_step_index_counts_every_step_and_rejects_out_of_range() -> None:
    ev = three_steps()
    assert ev.step_index(-3) == 0
    with pytest.raises(IndexError):
        ev.step_index(3)


def test_last_step_and_clock_at() -> None:
    ev = three_steps()
    assert ev.last_step("ha_event") == 2
    assert ev.last_step("dnd") is None
    assert ev.clock_at(2) == ClockSet(step=0, hour=22, tz="Etc/GMT-7")
    assert evidence(step_started=[0.0], step_kinds=["ha_event"]).clock_at(0) is None


def test_reflex_call_done_is_arrival_plus_latency() -> None:
    assert ReflexCall(t=2.0, latency_ms=500, decision="none").done == 2.5


def test_step_kinds_are_the_scenario_step_keys() -> None:
    assert STEP_KINDS == ("user", "ha_event", "wait", "clock", "advance_trigger", "dnd")


def test_new_evidence_round_trips_through_json() -> None:
    # tasks.py stores Evidence in Inspect's sample store as JSON and reads it back.
    created = datetime(2026, 10, 8, 18, 0, tzinfo=UTC)
    ev = evidence(
        step_started=[0.0, 5.0],
        step_kinds=["user", "advance_trigger"],
        reflex=[ReflexCall(t=1.0, latency_ms=900, decision="act", tool="home.light_turn_off")],
        triggers_created=[
            TriggerRecord(
                t=1.0,
                trigger_id="t1",
                trigger_type="time",
                name="Laundry",
                created_by="tool-call",
                conditions={"run_at": "2026-10-08T12:20:00-06:00"},
                created_at=created,
            )
        ],
        triggers_fired=[
            TriggerFire(
                t=6.0,
                trigger_id="t1",
                name="Laundry",
                trigger_type="time",
                urgency="informational",
                fired_by="engine",
            )
        ],
        notifications=[
            NotificationRecord(
                t=6.5, title="Trigger: Laundry", urgency="informational", source="trigger-engine"
            )
        ],
        deferred=[NotificationRecord(t=None, title="x", urgency="important", source="librarian")],
        advances=[Advance(step=1, trigger_id="t1", name="Laundry", t=5.1)],
    )
    assert Evidence.model_validate(ev.model_dump(mode="json")) == ev
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_evidence.py -q`
Expected: FAIL with `ImportError: cannot import name 'STEP_KINDS'`.

- [ ] **Step 3: Implement**

In `evals/harness/evidence.py`, add `from datetime import datetime  # noqa: TC003 — Pydantic resolves TriggerRecord.created_at` after `from typing import Any, Literal`. Then add, after `Role`:

```python
Decision = Literal["act", "ask", "none", "invalid"]
Urgency = Literal["informational", "important", "urgent"]
StepKind = Literal["user", "ha_event", "wait", "clock", "advance_trigger", "dnd"]
STEP_KINDS: tuple[StepKind, ...] = ("user", "ha_event", "wait", "clock", "advance_trigger", "dnd")
```

Add these models before `class Evidence`:

```python
class ReflexCall(BaseModel):
    """One System 1 call, parsed the way Reflex parses it (``core.reflex.decision``)."""

    t: float  # when the request reached the proxy
    latency_ms: float
    decision: Decision
    reason: str = ""
    tool: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    # Entity and area ids the proposal names, plus each named entity's area and each named
    # area's entities in the tool's domain, so a check can name either.
    targets: list[str] = Field(default_factory=list)
    problem: str | None = None
    local_hour: int | None = None  # the hour the prompt's clock line showed

    @property
    def done(self) -> float:
        """When System 1's reply came back: Reflex has decided."""
        return self.t + self.latency_ms / 1000


class TriggerRecord(BaseModel):
    """A trigger System 2 created through its tool (``TriggerCreated`` on alfred:events)."""

    t: float
    trigger_id: str
    trigger_type: str
    name: str
    created_by: str
    conditions: dict[str, Any] = Field(default_factory=dict)  # normalised: run_at, never a delay
    urgency: str = "informational"
    one_shot: bool = False
    created_at: datetime  # the event's own timestamp, the base a relative delay ran from


class TriggerFire(BaseModel):
    """``TriggerFired`` on alfred:events: a trigger with no action fired."""

    t: float
    trigger_id: str
    name: str
    trigger_type: str
    urgency: str
    fired_by: str


class NotificationRecord(BaseModel):
    t: float | None = None  # None for one read off the deferred list
    title: str
    body: str = ""
    urgency: str
    source: str


class Advance(BaseModel):
    """An ``advance_trigger`` step pulled this trigger's run_at to now at ``t``."""

    step: int
    trigger_id: str
    name: str
    t: float


class ClockSet(BaseModel):
    """A ``clock`` step set the user's zone to ``tz`` so the local hour was ``hour``."""

    step: int
    hour: int
    tz: str
```

Add the new fields after `llm_calls` in `Evidence`, and the methods after `calls_after_step`:

```python
    step_kinds: list[StepKind] = Field(default_factory=list)
    reflex: list[ReflexCall] = Field(default_factory=list)
    triggers_created: list[TriggerRecord] = Field(default_factory=list)
    triggers_fired: list[TriggerFire] = Field(default_factory=list)
    notifications: list[NotificationRecord] = Field(default_factory=list)  # dispatched
    deferred: list[NotificationRecord] = Field(default_factory=list)  # still held at the end
    advances: list[Advance] = Field(default_factory=list)
    clocks: list[ClockSet] = Field(default_factory=list)

    def step_index(self, step: int) -> int:
        """*step* (which counts every step, -1 the last) as a non-negative index."""
        n = len(self.step_started)
        if not -n <= step < n:
            raise IndexError(f"step {step} is outside the sample's {n} steps")
        return step % n

    def step_window(self, step: int) -> tuple[float, float]:
        """From the step's start to the next step's, or to the sample's end."""
        i = self.step_index(step)
        end = self.step_started[i + 1] if i + 1 < len(self.step_started) else self.ended_at
        return self.step_started[i], end

    def last_step(self, kind: StepKind) -> int | None:
        return next(
            (i for i in range(len(self.step_kinds) - 1, -1, -1) if self.step_kinds[i] == kind),
            None,
        )

    def clock_at(self, step: int) -> ClockSet | None:
        """The clock a step ran under: the last clock step at or before it."""
        i = self.step_index(step)
        return next((c for c in reversed(self.clocks) if c.step <= i), None)
```

In `tests/evals/harness/factories.py`, give `evidence()` a final `**extra: Any` parameter and pass it into `Evidence(...)` as `**extra`.

- [ ] **Step 4: Run the tests and watch them pass**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_evidence.py tests/evals/harness/test_checks.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff check --fix evals/harness/evidence.py tests/evals/harness
.venv/bin/ruff format evals/harness/evidence.py tests/evals/harness
git add evals/harness/evidence.py tests/evals/harness/factories.py tests/evals/harness/test_evidence.py
git commit -m "feat(evals): evidence for reflex decisions, triggers, notifications and step kinds"
```

---

### Task 3: Reflex checks and `prompt_not_contains`

**Files:**
- Create: `evals/harness/checks/reflex.py`
- Modify:
  - `evals/harness/checks/reply.py`: extract `Needles`
  - `evals/harness/checks/llm.py`: add `prompt_not_contains` and `message_text`
  - `evals/harness/checks/__init__.py`
- Test:
  - `tests/evals/harness/test_checks_reflex.py` (new)
  - `tests/evals/harness/test_checks.py`

**Interfaces:**
- Consumes (Task 2): `Evidence.step_window`, `last_step`, `step_index` and `clock_at`; `ReflexCall`.
- Produces:
  - `ReflexDecisionParams(decision: list[Decision], tool: str | None, target: str | None, at_step: int | None)`. A single string `decision` becomes a one-element list.
  - `ReflexNotProposedParams(tool: str, target: str | None, decision: list[Decision] = ["act", "ask"], at_step: int | None)`
  - `reflex_decision(ev, p)` and `reflex_not_proposed(ev, p)`
  - `Needles` (the fields `text`, `any`/`any_of`, `regex`) and `ReplyTextParams(Needles)`
  - `hit(p: Needles, text) -> str | None` and `needle_text(p: Needles) -> str`, in `reply.py`
  - `PromptParams(Needles)`, with `role: Role = "system2"`
  - `prompt_not_contains(ev, p)`
  - `message_text(message: dict) -> str`, in `llm.py`
  - Registry names: `reflex_decision`, `reflex_not_proposed`, `prompt_not_contains`

**`at_step` counts every step, like `after_step`.** The name is deliberately not `step`: `step` counts user steps, and `tests/evals/test_goldens_load.py` checks every `step` field against the user-step count. When `at_step` is omitted, it defaults to the golden's last `ha_event` step.

- [ ] **Step 1: Write the failing tests** (`tests/evals/harness/test_checks_reflex.py`)

```python
from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from evals.harness.checks import run_check
from evals.harness.checks.reflex import ReflexDecisionParams, ReflexNotProposedParams
from evals.harness.evidence import ClockSet, Evidence, ReflexCall, StepKind
from tests.evals.harness.factories import evidence


def rc(
    decision: str,
    tool: str | None = None,
    targets: tuple[str, ...] = (),
    t: float = 1.0,
    hour: int | None = None,
    reason: str = "because",
) -> ReflexCall:
    return ReflexCall(
        t=t,
        latency_ms=800,
        decision=decision,  # type: ignore[arg-type]
        tool=tool,
        targets=list(targets),
        local_hour=hour,
        reason=reason,
    )


def ev(
    *calls: ReflexCall,
    kinds: tuple[StepKind, ...] = ("ha_event",),
    starts: tuple[float, ...] = (0.0,),
    clocks: tuple[ClockSet, ...] = (),
) -> Evidence:
    return evidence(
        step_started=list(starts), step_kinds=list(kinds), reflex=list(calls), clocks=list(clocks)
    )


def decide(e: Evidence, **params: Any) -> tuple[str, str]:
    r = run_check("reflex_decision", ReflexDecisionParams.model_validate(params), e)
    return r.status, r.reason


def not_proposed(e: Evidence, **params: Any) -> tuple[str, str]:
    r = run_check("reflex_not_proposed", ReflexNotProposedParams.model_validate(params), e)
    return r.status, r.reason


def test_a_decision_in_the_set_passes() -> None:
    e = ev(rc("ask", "home.light_turn_off", ("light.living_room_lamp", "living_room")))
    assert decide(e, decision=["act", "ask"])[0] == "pass"
    assert decide(e, decision=["act", "ask"], tool="home_light_turn_off")[0] == "pass"
    assert decide(e, decision=["act", "ask"], target="living_room")[0] == "pass"


def test_the_wrong_tool_or_target_fails_and_says_what_system1_decided() -> None:
    e = ev(rc("act", "home.light_turn_on", ("light.bedroom_lamp", "bedroom")))
    status, reason = decide(e, decision=["act"], tool="home.light_turn_off")
    assert status == "fail" and "act home.light_turn_on" in reason
    assert decide(e, decision=["act"], target="living_room")[0] == "fail"


def test_none_passes_when_system1_was_not_called_and_says_so() -> None:
    status, reason = decide(ev(), decision="none")
    assert status == "pass" and "not called" in reason
    assert decide(ev(), decision=["act", "ask"])[0] == "fail"


def test_calls_outside_the_step_window_do_not_count() -> None:
    e = ev(
        rc("act", "home.light_turn_off", t=5.0),
        kinds=("ha_event", "ha_event"),
        starts=(0.0, 10.0),
    )
    assert decide(e, decision="none")[0] == "pass"  # default: the last ha_event step
    assert decide(e, decision="none", at_step=0)[0] == "fail"


def test_every_call_in_the_window_must_fit() -> None:
    e = ev(rc("none", t=1.0), rc("act", "home.light_turn_on", t=2.0))
    assert decide(e, decision="none")[0] == "fail"


def test_reflex_check_errors_when_the_clock_did_not_take() -> None:
    clock = ClockSet(step=0, hour=22, tz="Etc/GMT-7")
    kinds: tuple[StepKind, ...] = ("clock", "ha_event")
    took = ev(rc("none", t=11.0, hour=22), kinds=kinds, starts=(0.0, 10.0), clocks=(clock,))
    assert decide(took, decision="none")[0] == "pass"
    for hour in (16, None):
        wrong = ev(rc("none", t=11.0, hour=hour), kinds=kinds, starts=(0.0, 10.0), clocks=(clock,))
        status, reason = decide(wrong, decision="none")
        assert status == "error" and "22" in reason
        assert not_proposed(wrong, tool="home.light_turn_on")[0] == "error"


def test_not_proposed_fails_on_the_named_tool_and_target_only() -> None:
    e = ev(rc("act", "home.light_turn_on", ("light.living_room_ceiling", "living_room")))
    status, reason = not_proposed(e, tool="home.light_turn_on", target="light.living_room_ceiling")
    assert status == "fail" and "light_turn_on" in reason
    assert not_proposed(e, tool="home.light_turn_on", target="light.bedroom_lamp")[0] == "pass"
    assert not_proposed(e, tool="home.light_turn_off")[0] == "pass"
    asked = ev(rc("ask", "home.light_turn_on"))
    assert not_proposed(asked, tool="home.light_turn_on", decision="act")[0] == "pass"


def test_decision_params_take_one_string_and_reject_unknown_decisions() -> None:
    assert ReflexDecisionParams.model_validate({"decision": "none"}).decision == ["none"]
    with pytest.raises(ValidationError):
        ReflexDecisionParams.model_validate({"decision": "maybe"})
    with pytest.raises(ValidationError):
        ReflexDecisionParams.model_validate({"decision": []})
    with pytest.raises(ValidationError):
        ReflexDecisionParams.model_validate({"decision": "none", "step": 0})
```

Append these to `tests/evals/harness/test_checks.py`:

```python
from evals.harness.checks.llm import PromptParams
from evals.harness.evidence import LlmCall


def prompted(role: str, *contents: object) -> LlmCall:
    return LlmCall(
        t=1.0,
        role=role,  # type: ignore[arg-type]
        latency_ms=1.0,
        status=200,
        messages=[{"role": "user", "content": c} for c in contents],
    )


def test_prompt_not_contains_reads_every_prompt_of_the_role() -> None:
    clean = prompted("system1", "House: living room lamp on")
    noisy = prompted("system1", [{"type": "text", "text": "entity_picture: /api/x"}])
    other = prompted("system2", "button.restart")
    p = PromptParams.model_validate({"role": "system1", "any": ["entity_picture", "button."]})
    assert run_check("prompt_not_contains", p, evidence(llm_calls=[clean, other])).status == "pass"
    failed = run_check("prompt_not_contains", p, evidence(llm_calls=[clean, noisy]))
    assert failed.status == "fail" and "entity_picture" in failed.reason


def test_prompt_not_contains_fails_when_the_role_was_never_called() -> None:
    p = PromptParams.model_validate({"role": "system1", "text": "notify."})
    result = run_check("prompt_not_contains", p, evidence(llm_calls=[]))
    assert result.status == "fail" and "no system1 call" in result.reason
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_checks_reflex.py tests/evals/harness/test_checks.py -q`
Expected: FAIL. The import errors are `No module named 'evals.harness.checks.reflex'` and `cannot import name 'PromptParams'`.

- [ ] **Step 3: Extract `Needles` in `evals/harness/checks/reply.py`**

Replace `ReplyTextParams`, `_hit` and `_needle` with the code below. Update `reply_contains` and `reply_not_contains` to call `hit` and `needle_text`. `_replies`, `_quote` and `_seen` stay as they are.

```python
class Needles(BaseModel):
    """Exactly one of ``text``, ``any`` or ``regex``: what to look for in a text."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    text: str | None = None
    any_of: list[str] | None = Field(default=None, alias="any")
    regex: str | None = None

    @field_validator("regex")
    @classmethod
    def _regex_compiles(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"regex {value!r} does not compile: {exc}") from exc
        return value

    @model_validator(mode="after")
    def _exactly_one_needle(self) -> Self:
        if sum(x is not None for x in (self.text, self.any_of, self.regex)) != 1:
            raise ValueError("give exactly one of text, any, regex")
        needles = [n for n in (self.text, self.regex, *(self.any_of or [])) if n is not None]
        if self.any_of == [] or any(not n.strip() for n in needles):
            raise ValueError(
                "needles must not be empty or blank: an empty one matches every text, "
                "and a blank one nearly every text"
            )
        return self


class ReplyTextParams(Needles):
    step: int | Literal["any"] = -1


def hit(p: Needles, text: str) -> str | None:
    """The needle found in *text* (case-insensitive), or None."""
    lowered = text.lower()
    if p.text is not None:
        return p.text if p.text.lower() in lowered else None
    if p.any_of is not None:
        return next((n for n in p.any_of if n.lower() in lowered), None)
    assert p.regex is not None
    m = re.search(p.regex, text, re.IGNORECASE)
    return m.group(0) if m else None


def needle_text(p: Needles) -> str:
    if p.text is not None:
        return p.text
    if p.any_of is not None:
        return " | ".join(p.any_of)
    return f"/{p.regex}/"
```

If an existing test asserts the old wording "matches every reply", change that expected text to "matches every text".

- [ ] **Step 4: Add `prompt_not_contains` to `evals/harness/checks/llm.py`**

Import `Needles`, `hit` and `needle_text` from `evals.harness.checks.reply`, then add:

```python
class PromptParams(Needles):
    role: Role = "system2"


def message_text(message: dict[str, Any]) -> str:
    """A chat message's text: its string content, or its text parts joined."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text", "")) for part in content if isinstance(part, dict)
        )
    return ""


def prompt_not_contains(evidence: Evidence, p: PromptParams) -> CheckResult:
    calls = [c for c in evidence.llm_calls if c.role == p.role]
    if not calls:
        # Vacuously clean is no evidence: the prompt the golden is about was never sent.
        return failed("prompt_not_contains", f"no {p.role} call was recorded")
    for call in calls:
        for message in call.messages:
            if (found := hit(p, message_text(message))) is not None:
                return failed("prompt_not_contains", f"a {p.role} prompt contains {found!r}")
    return passed(
        "prompt_not_contains", f"{needle_text(p)} absent from {len(calls)} {p.role} prompt(s)"
    )
```

- [ ] **Step 5: Create `evals/harness/checks/reflex.py`**

```python
"""Checks over System 1's decisions. Reflex runs in shadow mode: it decides, nothing acts.

The calls a check reads are the System 1 calls that reached the proxy during one step,
an ``ha_event`` (``at_step``, which counts every step; default the golden's last one).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from evals.harness.checks.llm import normalize_tool
from evals.harness.checks.result import CheckResult, failed, passed
from evals.harness.evidence import Decision  # noqa: TC001 — Pydantic resolves the params

if TYPE_CHECKING:
    from evals.harness.evidence import Evidence, ReflexCall


def _as_list(value: Any) -> Any:
    return [value] if isinstance(value, str) else value


Decisions = Annotated[list[Decision], BeforeValidator(_as_list), Field(min_length=1)]


class _AtStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    at_step: int | None = None


class ReflexDecisionParams(_AtStep):
    decision: Decisions
    tool: str | None = None
    target: str | None = None  # an entity id or an area id


class ReflexNotProposedParams(_AtStep):
    tool: str
    target: str | None = None
    decision: Decisions = Field(default_factory=lambda: ["act", "ask"])


def _step(evidence: Evidence, at_step: int | None) -> int | None:
    return evidence.last_step("ha_event") if at_step is None else evidence.step_index(at_step)


def _calls(evidence: Evidence, step: int) -> list[ReflexCall]:
    start, end = evidence.step_window(step)
    return [c for c in evidence.reflex if start <= c.t < end]


def _clock_problem(evidence: Evidence, step: int, calls: list[ReflexCall]) -> str | None:
    """Why these calls cannot be judged against the golden's clock, or None."""
    clock = evidence.clock_at(step)
    if clock is None:
        return None
    for c in calls:
        if c.local_hour != clock.hour:
            saw = "no clock line" if c.local_hour is None else f"{c.local_hour:02d}:xx"
            return (
                f"the clock step set {clock.hour:02d}:xx ({clock.tz}), but System 1's prompt "
                f"showed {saw}: the evidence is about the wrong time of day"
            )
    return None


def _tool_is(c: ReflexCall, tool: str) -> bool:
    return c.tool is not None and normalize_tool(c.tool) == normalize_tool(tool)


def _describe(c: ReflexCall) -> str:
    what = c.decision if c.tool is None else f"{c.decision} {c.tool}"
    named = [t for t in c.targets if "." in t] or c.targets
    if named:
        what += f" on {', '.join(named)}"
    why = c.problem or c.reason
    return f"{what} ({why})" if why else what


def _fits(c: ReflexCall, p: ReflexDecisionParams) -> bool:
    if c.decision not in p.decision:
        return False
    if c.decision in ("act", "ask"):
        if p.tool is not None and not _tool_is(c, p.tool):
            return False
        if p.target is not None and p.target not in c.targets:
            return False
    return True


def _want(p: ReflexDecisionParams) -> str:
    want = "/".join(p.decision)
    if p.tool is not None:
        want += f" {p.tool}"
    if p.target is not None:
        want += f" on {p.target}"
    return want


def reflex_decision(evidence: Evidence, p: ReflexDecisionParams) -> CheckResult:
    name = "reflex_decision"
    step = _step(evidence, p.at_step)
    if step is None:
        return failed(name, "the sample has no ha_event step")
    calls = _calls(evidence, step)
    if (problem := _clock_problem(evidence, step, calls)) is not None:
        return CheckResult(name=name, status="error", reason=problem)
    if not calls:
        if "none" in p.decision:
            return passed(name, f"System 1 was not called for step {step}: Reflex let it pass")
        return failed(name, f"System 1 was not called for step {step}; wanted {_want(p)}")
    seen = "; ".join(_describe(c) for c in calls)
    if all(_fits(c, p) for c in calls):
        return passed(name, seen)
    return failed(name, f"wanted {_want(p)}; System 1 decided {seen}")


def reflex_not_proposed(evidence: Evidence, p: ReflexNotProposedParams) -> CheckResult:
    name = "reflex_not_proposed"
    step = _step(evidence, p.at_step)
    if step is None:
        return failed(name, "the sample has no ha_event step")
    calls = _calls(evidence, step)
    if (problem := _clock_problem(evidence, step, calls)) is not None:
        return CheckResult(name=name, status="error", reason=problem)
    for c in calls:
        if (
            c.decision in p.decision
            and _tool_is(c, p.tool)
            and (p.target is None or p.target in c.targets)
        ):
            return failed(name, f"System 1 proposed {_describe(c)}")
    seen = "; ".join(_describe(c) for c in calls) or "it was not called"
    on = "" if p.target is None else f" on {p.target}"
    return passed(name, f"no {'/'.join(p.decision)} {p.tool}{on} (System 1: {seen})")
```

- [ ] **Step 6: Register the three checks** in `evals/harness/checks/__init__.py`

Import `reflex` with the other check modules. Add these entries to `DETERMINISTIC`:

```python
    "reflex_decision": (reflex.ReflexDecisionParams, reflex.reflex_decision),
    "reflex_not_proposed": (reflex.ReflexNotProposedParams, reflex.reflex_not_proposed),
    "prompt_not_contains": (llm.PromptParams, llm.prompt_not_contains),
```

- [ ] **Step 7: Run the tests and watch them pass**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_checks_reflex.py tests/evals/harness/test_checks.py tests/evals/test_goldens_load.py -q`
Expected: PASS. The goldens still load, because `Needles` kept every field name.

- [ ] **Step 8: Commit**

```bash
.venv/bin/ruff check --fix evals/harness/checks tests/evals/harness
.venv/bin/ruff format evals/harness/checks tests/evals/harness
git add evals/harness/checks tests/evals/harness/test_checks_reflex.py tests/evals/harness/test_checks.py
git commit -m "feat(evals): reflex_decision, reflex_not_proposed and prompt_not_contains checks"
```

---
### Task 4: Trigger and notification checks, and two new latency metrics

**Files:**
- Create:
  - `evals/harness/checks/triggers.py`
  - `evals/harness/checks/notifications.py`
- Modify:
  - `evals/harness/checks/latency.py`
  - `evals/harness/checks/__init__.py`
  - `evals/harness/scenario.py`: one line, the `needs_reply` call
- Test: `tests/evals/harness/test_checks_triggers.py` (new)

**Interfaces:**
- Consumes (Task 2): `TriggerRecord`, `TriggerFire`, `NotificationRecord`, `Advance`, `ReflexCall`, `Urgency`, `StepKind`; `Evidence.step_index`, `step_window` and `last_step`.
- Consumes (Task 1): `value_matches`, `is_approx`, `validate_expected`, `describe`.
- Produces:
  - `TriggerCreatedParams(type, name, conditions, run_in_seconds, at_local: AtLocal, urgency, one_shot)`
  - `TriggerTypeParams(type)`, the params for `trigger_not_created`
  - `TriggerFiredParams(name, after_step, within_s)`
  - `NotificationParams(urgency, source, text, deferred=False, after_step)`
  - `LatencyParams(metric: "reply_ms" | "reflex_ms" | "reminder_fire_ms", max, step: int | None, at_step: int | None)`
  - `TRIGGER_SOURCE = "trigger-engine"`, in `latency.py`
  - In `evals/harness/checks/__init__.py`:
    - `needs_reply(name, params) -> bool`
    - `step_kind_needed(name, params) -> StepKind | None`
    - `watches_reflex(name, params) -> bool`
  - `NEEDS_REPLY` is removed.
  - Registry names: `trigger_created`, `trigger_not_created`, `trigger_fired`, `notification`.

- [ ] **Step 1: Write the failing tests** (`tests/evals/harness/test_checks_triggers.py`)

```python
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from evals.harness.checks import needs_reply, run_check, step_kind_needed, watches_reflex
from evals.harness.checks.latency import LatencyParams
from evals.harness.checks.llm import PromptParams
from evals.harness.checks.notifications import NotificationParams
from evals.harness.checks.triggers import (
    TriggerCreatedParams,
    TriggerFiredParams,
    TriggerTypeParams,
)
from evals.harness.evidence import (
    Advance,
    Evidence,
    NotificationRecord,
    ReflexCall,
    TriggerFire,
    TriggerRecord,
)
from tests.evals.harness.factories import evidence

CREATED = datetime(2026, 10, 8, 18, 0, tzinfo=UTC)
CRON = r"0 7 \* \* (1-5|mon-fri)"


def rec(
    conditions: dict[str, Any],
    *,
    ttype: str = "time",
    name: str = "Laundry reminder",
    urgency: str = "informational",
    tid: str = "t1",
) -> TriggerRecord:
    return TriggerRecord(
        t=1.0,
        trigger_id=tid,
        trigger_type=ttype,
        name=name,
        created_by="tool-call",
        conditions=conditions,
        urgency=urgency,
        created_at=CREATED,
    )


def due_in(seconds: float) -> dict[str, Any]:
    """Conditions as normalize_conditions stores them: run_at in the user's zone."""
    run_at = (CREATED + timedelta(seconds=seconds)).astimezone(ZoneInfo("America/Denver"))
    return {"run_at": run_at.isoformat()}


def created(e: Evidence, **params: Any) -> tuple[str, str]:
    r = run_check("trigger_created", TriggerCreatedParams.model_validate(params), e)
    return r.status, r.reason


def test_trigger_created_measures_a_relative_delay_from_creation() -> None:
    e = evidence(triggers_created=[rec(due_in(1230))])
    assert created(e, type="time", run_in_seconds={"approx": 1200, "tol": 60})[0] == "pass"
    status, reason = created(e, run_in_seconds={"approx": 1200, "tol": 10})
    assert status == "fail" and "(in 1230s)" in reason


def test_at_local_reads_run_at_in_the_given_zone() -> None:
    new_york = evidence(triggers_created=[rec({"run_at": "2026-10-08T19:00:00-04:00"})])
    utc = evidence(triggers_created=[rec({"run_at": "2026-10-08T19:00:00+00:00"})])
    at = {"time": "19:00", "tz": "America/New_York"}
    assert created(new_york, at_local=at)[0] == "pass"
    assert created(utc, at_local=at)[0] == "fail"


def test_conditions_match_a_cron_pattern_and_sensor_fields() -> None:
    cron = evidence(triggers_created=[rec({"cron": "0 7 * * MON-FRI"})])
    assert created(cron, conditions={"cron": {"regex": CRON}})[0] == "pass"
    door = {"entity_id": "binary_sensor.front_door", "state_match": "on"}
    sensor = evidence(triggers_created=[rec(door, ttype="sensor")])
    assert created(sensor, type="sensor", conditions=door)[0] == "pass"
    assert created(sensor, conditions={"state_match": "open"})[0] == "fail"
    assert created(sensor, type="time")[0] == "fail"


def test_name_and_urgency_narrow_the_match() -> None:
    e = evidence(triggers_created=[rec({}, name="Airport", urgency="urgent")])
    assert created(e, name="airport", urgency="urgent")[0] == "pass"
    assert created(e, urgency="informational")[0] == "fail"
    assert created(evidence(), type="time")[1].endswith("created: none")


@pytest.mark.parametrize(
    "params",
    [
        {"run_in_seconds": {"regex": "x"}},
        {"at_local": {"time": "7pm", "tz": "America/New_York"}},
        {"at_local": {"time": "19:00", "tz": "Mars/Base"}},
        {"conditions": {"cron": {"regex": "("}}},
        {"kind": "time"},
    ],
)
def test_trigger_created_params_reject_bad_input(params: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        TriggerCreatedParams.model_validate(params)


def test_trigger_not_created() -> None:
    sensor = evidence(triggers_created=[rec({}, ttype="sensor")])
    check = "trigger_not_created"
    assert run_check(check, TriggerTypeParams(), evidence()).status == "pass"
    assert run_check(check, TriggerTypeParams(type="time"), sensor).status == "pass"
    assert run_check(check, TriggerTypeParams(type="sensor"), sensor).status == "fail"


def fire(t: float, tid: str = "t1", name: str = "Laundry reminder") -> TriggerFire:
    return TriggerFire(
        t=t, trigger_id=tid, name=name, trigger_type="time", urgency="informational",
        fired_by="engine",
    )


def test_trigger_fired_ignores_triggers_from_other_samples() -> None:
    p = TriggerFiredParams()
    stale = evidence(triggers_fired=[fire(5.0, tid="from-an-earlier-epoch")])
    result = run_check("trigger_fired", p, stale)
    assert result.status == "fail" and "created in this sample" in result.reason
    mine = evidence(triggers_created=[rec({})], triggers_fired=[fire(5.0)])
    assert run_check("trigger_fired", p, mine).status == "pass"


def test_trigger_fired_within_counts_from_after_step() -> None:
    def ev(t: float) -> Evidence:
        return evidence(
            step_started=[0.0, 10.0],
            step_kinds=["user", "advance_trigger"],
            triggers_created=[rec({})],
            triggers_fired=[fire(t)],
        )

    p = TriggerFiredParams(after_step=1, within_s=5)
    assert run_check("trigger_fired", p, ev(13.0)).status == "pass"
    late = run_check("trigger_fired", p, ev(16.0))
    assert late.status == "fail" and "6.0s after step 1" in late.reason
    early = run_check("trigger_fired", p, ev(9.0))  # fired before the step started
    assert early.status == "fail"
    with pytest.raises(ValidationError, match="after_step"):
        TriggerFiredParams(within_s=5)


def note(t: float | None, title: str = "Trigger: Laundry reminder", **kw: Any) -> NotificationRecord:
    return NotificationRecord(
        t=t, title=title, urgency=kw.get("urgency", "informational"),
        source=kw.get("source", "trigger-engine"),
    )


def test_notification_sent_deferred_and_after_step() -> None:
    e = evidence(
        step_started=[0.0, 10.0],
        notifications=[note(4.0, urgency="urgent"), note(12.0, title="Trigger: Vet")],
        deferred=[note(None, title="Trigger: Plants")],
    )

    def check(**params: Any) -> str:
        return run_check("notification", NotificationParams.model_validate(params), e).status

    assert check(urgency="urgent") == "pass"
    assert check(urgency="urgent", after_step=1) == "fail"
    assert check(text="vet", after_step=1, source="trigger-engine") == "pass"
    assert check(text="plants") == "fail"  # held, not sent
    assert check(text="plants", deferred=True) == "pass"
    with pytest.raises(ValidationError, match="deferred"):
        NotificationParams(deferred=True, after_step=0)


def test_reflex_ms_runs_from_the_event_to_system1s_reply() -> None:
    def ev(*calls: ReflexCall) -> Evidence:
        return evidence(
            step_started=[0.0, 10.0], step_kinds=["ha_event", "ha_event"], reflex=list(calls)
        )

    call = ReflexCall(t=10.2, latency_ms=600, decision="none")
    fast = LatencyParams.model_validate({"metric": "reflex_ms", "max": 1000})
    slow = LatencyParams.model_validate({"metric": "reflex_ms", "max": 500})
    assert run_check("latency", fast, ev(call)).status == "pass"
    result = run_check("latency", slow, ev(call))
    assert result.status == "fail" and "800 ms" in result.reason
    assert "not called" in run_check("latency", fast, ev()).reason


def test_reminder_fire_ms_runs_from_the_advance_to_its_notification() -> None:
    p = LatencyParams.model_validate({"metric": "reminder_fire_ms", "max": 5000})
    e = evidence(
        step_started=[0.0, 10.0],
        step_kinds=["user", "advance_trigger"],
        advances=[Advance(step=1, trigger_id="t1", name="Laundry reminder", t=10.0)],
        notifications=[note(9.0), note(12.5)],
    )
    result = run_check("latency", p, e)
    assert result.status == "pass" and "2500 ms" in result.reason
    no_advance = e.model_copy(update={"advances": []})
    assert "brought forward" in run_check("latency", p, no_advance).reason


def test_latency_params_tie_the_index_to_the_metric() -> None:
    LatencyParams.model_validate({"metric": "reply_ms", "max": 1, "step": 0})
    LatencyParams.model_validate({"metric": "reflex_ms", "max": 1, "at_step": 0})
    with pytest.raises(ValidationError, match="at_step"):
        LatencyParams.model_validate({"metric": "reflex_ms", "max": 1, "step": 0})
    with pytest.raises(ValidationError, match="reply index"):
        LatencyParams.model_validate({"metric": "reply_ms", "max": 1, "at_step": 0})


def test_which_checks_need_a_reply_a_step_kind_or_system1() -> None:
    reply = LatencyParams.model_validate({"metric": "reply_ms", "max": 1})
    reflex = LatencyParams.model_validate({"metric": "reflex_ms", "max": 1})
    fire_ms = LatencyParams.model_validate({"metric": "reminder_fire_ms", "max": 1})
    assert needs_reply("latency", reply) and not needs_reply("latency", reflex)
    assert step_kind_needed("latency", reflex) == "ha_event"
    assert step_kind_needed("latency", fire_ms) == "advance_trigger"
    assert step_kind_needed("latency", reply) is None
    prompt = PromptParams.model_validate({"role": "system1", "text": "notify."})
    assert watches_reflex("prompt_not_contains", prompt) and watches_reflex("latency", reflex)
    assert not watches_reflex("latency", fire_ms)
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_checks_triggers.py -q`
Expected: FAIL with `ImportError: cannot import name 'needs_reply'`.

- [ ] **Step 3: Create `evals/harness/checks/triggers.py`**

```python
"""Checks over the triggers System 2 created in the sample, and their fires.

A trigger's ``conditions`` are as the engine normalised them: a relative delay is
already a ``run_at`` in the user's zone, so ``run_in_seconds`` is measured from the
trigger's creation to that ``run_at``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evals.harness.checks.matching import describe, is_approx, validate_expected, value_matches
from evals.harness.checks.result import CheckResult, failed, passed
from evals.harness.evidence import Urgency  # noqa: TC001 — Pydantic resolves the params

if TYPE_CHECKING:
    from evals.harness.evidence import Evidence, TriggerRecord

TriggerType = Literal["time", "sensor", "composite"]


class AtLocal(BaseModel):
    """The trigger's ``run_at``, shown in ``tz``, reads ``time`` (``HH:MM``)."""

    model_config = ConfigDict(extra="forbid")
    time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    tz: str

    @field_validator("tz")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone {value!r}") from exc
        return value


class TriggerCreatedParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: TriggerType | None = None
    name: str | None = None  # a substring of the trigger's name, case-insensitive
    conditions: dict[str, Any] = Field(default_factory=dict)
    run_in_seconds: float | dict[str, float] | None = None  # a number or {approx, tol}
    at_local: AtLocal | None = None
    urgency: Urgency | None = None
    one_shot: bool | None = None

    @field_validator("conditions")
    @classmethod
    def _patterns_compile(cls, value: dict[str, Any]) -> dict[str, Any]:
        validate_expected(value)
        return value

    @field_validator("run_in_seconds")
    @classmethod
    def _number_or_approx(cls, value: object) -> object:
        if isinstance(value, dict) and not is_approx(value):
            raise ValueError("run_in_seconds is a number or {approx, tol}")
        return value


class TriggerTypeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: TriggerType | None = None


class TriggerFiredParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    after_step: int | None = None
    within_s: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _bound_needs_a_start(self) -> Self:
        if self.within_s is not None and self.after_step is None:
            raise ValueError("within_s counts from after_step's start: give after_step too")
        return self


def _run_at(r: TriggerRecord) -> datetime | None:
    raw = r.conditions.get("run_at")
    if not isinstance(raw, str):
        return None
    try:
        when = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return when if when.tzinfo is not None else None


def _run_in(r: TriggerRecord) -> float | None:
    when = _run_at(r)
    if when is None:
        return None
    created = r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=UTC)
    return (when - created).total_seconds()


def _fits(r: TriggerRecord, p: TriggerCreatedParams) -> bool:
    if p.type is not None and r.trigger_type != p.type:
        return False
    if p.name is not None and p.name.lower() not in r.name.lower():
        return False
    if not value_matches(p.conditions, r.conditions):
        return False
    if p.run_in_seconds is not None:
        seconds = _run_in(r)
        if seconds is None or not value_matches(p.run_in_seconds, seconds):
            return False
    if p.at_local is not None:
        when = _run_at(r)
        local = None if when is None else f"{when.astimezone(ZoneInfo(p.at_local.tz)):%H:%M}"
        if local != p.at_local.time:
            return False
    if p.urgency is not None and r.urgency != p.urgency:
        return False
    return p.one_shot is None or r.one_shot is p.one_shot


def _describe(r: TriggerRecord) -> str:
    text = f"{r.trigger_type} {r.name!r} {describe(r.conditions)}"
    if (seconds := _run_in(r)) is not None:
        text += f" (in {seconds:.0f}s)"
    return f"{text} urgency={r.urgency}"


def _seen(records: list[TriggerRecord]) -> str:
    return "; ".join(_describe(r) for r in records) or "none"


def trigger_created(evidence: Evidence, p: TriggerCreatedParams) -> CheckResult:
    hits = [r for r in evidence.triggers_created if _fits(r, p)]
    if hits:
        return passed("trigger_created", _describe(hits[0]))
    want = describe(p.model_dump(exclude_none=True, exclude_defaults=True))
    return failed(
        "trigger_created", f"no trigger like {want}; created: {_seen(evidence.triggers_created)}"
    )


def trigger_not_created(evidence: Evidence, p: TriggerTypeParams) -> CheckResult:
    hits = [r for r in evidence.triggers_created if p.type is None or r.trigger_type == p.type]
    if hits:
        return failed("trigger_not_created", f"created {_seen(hits)}")
    kind = "" if p.type is None else f"{p.type} "
    return passed("trigger_not_created", f"no {kind}trigger created")


def trigger_fired(evidence: Evidence, p: TriggerFiredParams) -> CheckResult:
    """A fire of a trigger created in this sample: an earlier sample's trigger can still
    fire in this one's window, and is not this golden's evidence."""
    name = "trigger_fired"
    mine = {r.trigger_id for r in evidence.triggers_created}
    fires = [
        f
        for f in evidence.triggers_fired
        if f.trigger_id in mine and (p.name is None or p.name.lower() in f.name.lower())
    ]
    start: float | None = None
    if p.after_step is not None:
        start = evidence.step_started[evidence.step_index(p.after_step)]
        fires = [f for f in fires if f.t >= start]
    if not fires:
        since = "" if p.after_step is None else f" from step {p.after_step}"
        return failed(
            name,
            f"no trigger created in this sample fired{since} "
            f"(created: {_seen(evidence.triggers_created)})",
        )
    first = fires[0]
    if p.within_s is None or start is None:
        return passed(name, f"{first.name!r} fired")
    took = first.t - start
    if took > p.within_s:
        return failed(
            name,
            f"{first.name!r} fired {took:.1f}s after step {p.after_step}, bound {p.within_s:g}s",
        )
    return passed(name, f"{first.name!r} fired {took:.1f}s after step {p.after_step}")
```

- [ ] **Step 4: Create `evals/harness/checks/notifications.py`**

```python
"""Checks over notifications: dispatched in the sample, or still held by do-not-disturb.

``Notification`` carries no channel: the delivery workers route by urgency
(``core/notifications/delivery.py``), so urgency is what a golden can ask about.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from pydantic import BaseModel, ConfigDict, model_validator

from evals.harness.checks.matching import describe
from evals.harness.checks.result import CheckResult, failed, passed
from evals.harness.evidence import Urgency  # noqa: TC001 — Pydantic resolves the params

if TYPE_CHECKING:
    from evals.harness.evidence import Evidence, NotificationRecord


class NotificationParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    urgency: Urgency | None = None
    source: str | None = None
    text: str | None = None  # in the title or the body, case-insensitive
    deferred: bool = False
    after_step: int | None = None

    @model_validator(mode="after")
    def _held_has_no_send_time(self) -> Self:
        if self.deferred and self.after_step is not None:
            raise ValueError("a deferred notification was never sent: after_step does not apply")
        return self


def _fits(n: NotificationRecord, p: NotificationParams) -> bool:
    return (
        (p.urgency is None or n.urgency == p.urgency)
        and (p.source is None or n.source == p.source)
        and (p.text is None or p.text.lower() in f"{n.title}\n{n.body}".lower())
    )


def _describe(n: NotificationRecord) -> str:
    return f"{n.urgency} {n.title!r} from {n.source}"


def notification(evidence: Evidence, p: NotificationParams) -> CheckResult:
    where = "deferred" if p.deferred else "sent"
    pool = list(evidence.deferred if p.deferred else evidence.notifications)
    if p.after_step is not None:
        start = evidence.step_started[evidence.step_index(p.after_step)]
        pool = [n for n in pool if n.t is not None and n.t >= start]
    hits = [n for n in pool if _fits(n, p)]
    if hits:
        return passed("notification", f"{where}: {_describe(hits[0])}")
    want = describe(p.model_dump(exclude_none=True, exclude={"deferred"}))
    seen = "; ".join(_describe(n) for n in pool) or "none"
    return failed("notification", f"no {where} notification like {want}; {where}: {seen}")
```

- [ ] **Step 5: Rewrite `evals/harness/checks/latency.py`**

```python
"""Latency bounds: a reply, Reflex's decision on an event, and a due reminder's notification."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evals.harness.checks.result import CheckResult, failed, passed

if TYPE_CHECKING:
    from evals.harness.evidence import Evidence

TRIGGER_SOURCE = "trigger-engine"  # the source of the notification a trigger's fire sends


class LatencyParams(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    metric: Literal["reply_ms", "reflex_ms", "reminder_fire_ms"]
    max_ms: float = Field(gt=0, alias="max")
    # reply_ms: a reply index, one reply per user step (default -1, the last reply).
    step: int | None = None
    # reflex_ms, reminder_fire_ms: counts every step, like after_step. Default: the golden's
    # last ha_event (reflex_ms) or advance_trigger (reminder_fire_ms) step.
    at_step: int | None = None

    @model_validator(mode="after")
    def _index_fits_the_metric(self) -> Self:
        if self.metric == "reply_ms" and self.at_step is not None:
            raise ValueError("reply_ms takes step (a reply index), not at_step")
        if self.metric != "reply_ms" and self.step is not None:
            raise ValueError(f"{self.metric} takes at_step (it counts every step), not step")
        return self


Measured = tuple[float, str] | str  # (milliseconds, what was measured), or why nothing was


def _reply_ms(evidence: Evidence, step: int) -> Measured:
    try:
        reply = evidence.replies[step]
    except IndexError:
        return f"no reply at step {step}"
    return reply.latency_ms, "reply"


def _reflex_ms(evidence: Evidence, at_step: int | None) -> Measured:
    step = evidence.last_step("ha_event") if at_step is None else evidence.step_index(at_step)
    if step is None:
        return "the sample has no ha_event step"
    start, end = evidence.step_window(step)
    calls = [c for c in evidence.reflex if start <= c.t < end]
    if not calls:
        return f"System 1 was not called for step {step}"
    return (calls[0].done - start) * 1000, f"Reflex decided on step {step}"


def _reminder_fire_ms(evidence: Evidence, at_step: int | None) -> Measured:
    if at_step is None:
        step = evidence.last_step("advance_trigger")
    else:
        step = evidence.step_index(at_step)
    if step is None:
        return "the sample has no advance_trigger step"
    advance = next((a for a in evidence.advances if a.step == step), None)
    if advance is None:
        return f"no trigger was brought forward at step {step}"
    sent = [
        n.t
        for n in evidence.notifications
        if n.t is not None
        and n.t >= advance.t
        and n.source == TRIGGER_SOURCE
        and advance.name.lower() in n.title.lower()
    ]
    if not sent:
        return f"no notification for {advance.name!r} after it was due"
    return (sent[0] - advance.t) * 1000, f"{advance.name!r} notified"


def latency(evidence: Evidence, p: LatencyParams) -> CheckResult:
    match p.metric:
        case "reply_ms":
            measured = _reply_ms(evidence, -1 if p.step is None else p.step)
        case "reflex_ms":
            measured = _reflex_ms(evidence, p.at_step)
        case "reminder_fire_ms":
            measured = _reminder_fire_ms(evidence, p.at_step)
    if isinstance(measured, str):
        return failed("latency", measured)
    took, what = measured
    if took <= p.max_ms:
        return passed("latency", f"{what} in {took:.0f} ms (≤ {p.max_ms:.0f})")
    return failed("latency", f"{what} took {took:.0f} ms, bound {p.max_ms:.0f}")
```

The reply messages ("reply in … ms", "reply took … ms, bound …", "no reply at step N") are unchanged, so slice 1's latency tests still hold.

- [ ] **Step 6: Update the registry** (`evals/harness/checks/__init__.py`)

Import `notifications` and `triggers` with the other modules, and `StepKind` from `evals.harness.evidence`. Then:

- Add to `DETERMINISTIC`:

  ```python
      "trigger_created": (triggers.TriggerCreatedParams, triggers.trigger_created),
      "trigger_not_created": (triggers.TriggerTypeParams, triggers.trigger_not_created),
      "trigger_fired": (triggers.TriggerFiredParams, triggers.trigger_fired),
      "notification": (notifications.NotificationParams, notifications.notification),
  ```

- Replace `NEEDS_REPLY` with:

  ```python
  # Checks that read Alfred's reply, so a golden using one needs at least one user step.
  _READS_REPLY: frozenset[str] = frozenset({"judge", "reply_contains", "reply_not_contains"})
  _STEP_KIND_FOR_METRIC: dict[str, StepKind] = {
      "reflex_ms": "ha_event",
      "reminder_fire_ms": "advance_trigger",
  }


  def needs_reply(name: str, params: BaseModel) -> bool:
      if name == "latency":
          return getattr(params, "metric", None) == "reply_ms"
      return name in _READS_REPLY


  def step_kind_needed(name: str, params: BaseModel) -> StepKind | None:
      """The kind of step the check reads (its ``at_step``, default the golden's last)."""
      if name in ("reflex_decision", "reflex_not_proposed"):
          return "ha_event"
      if name == "latency":
          return _STEP_KIND_FOR_METRIC.get(str(getattr(params, "metric", "")))
      return None


  def watches_reflex(name: str, params: BaseModel) -> bool:
      """Whether the check reads System 1's calls, so the driver waits for them."""
      if name == "prompt_not_contains":
          return getattr(params, "role", None) == "system1"
      return step_kind_needed(name, params) == "ha_event"
  ```

- Move `from pydantic import BaseModel` out of `TYPE_CHECKING`. It is now used at runtime in signatures that mypy reads, and keeping it under `TYPE_CHECKING` is also fine with `from __future__ import annotations`. Follow ruff's verdict.
- Update `__all__`: drop `NEEDS_REPLY`, add `needs_reply`, `step_kind_needed` and `watches_reflex`.
- Run `grep -rn NEEDS_REPLY evals tests` and fix every hit.

In `evals/harness/scenario.py`:

- Import `needs_reply` instead of `NEEDS_REPLY`.
- Change the reply rule in `_coherent` to:

  ```python
          if any(needs_reply(c.name, c.params) for c in self.expect) and not any(
              isinstance(s, UserStep) for s in self.steps
          ):
              raise ValueError("reply and judge checks need at least one user step")
  ```

- [ ] **Step 7: Run the tests and watch them pass**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_checks_triggers.py tests/evals/harness/test_checks.py tests/evals/harness/test_scenario.py tests/evals/test_goldens_load.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
.venv/bin/ruff check --fix evals/harness tests/evals/harness
.venv/bin/ruff format evals/harness tests/evals/harness
git add evals/harness/checks evals/harness/scenario.py tests/evals/harness/test_checks_triggers.py
git commit -m "feat(evals): trigger, notification and reflex/reminder latency checks"
```

---

### Task 5: The `clock`, `advance_trigger` and `dnd` steps

**Files:**
- Modify: `evals/harness/scenario.py`
- Test: `tests/evals/harness/test_scenario.py`

**Interfaces:**
- Consumes:
  - from Task 4: `needs_reply`, `step_kind_needed`, `watches_reflex`;
  - from Task 2: `STEP_KINDS`, `StepKind`.
- Produces:
  - `Clock(hour: int 0..23)` and `ClockStep(clock: Clock)`
  - `AdvanceTrigger(name: str | None)` and `AdvanceTriggerStep(advance_trigger: AdvanceTrigger, settle: float | None)`. A bare `advance_trigger:` key means `{}`.
  - `DndStep(dnd: StrictBool, settle: float = 3.0)`
  - `step_kind(value) -> StepKind | None`, renamed from `_step_kind` and now public
  - `Scenario.watches_reflex -> bool`
  - Loader rules:
    - `at_step`, like `after_step`, must name a real step;
    - a check that reads a step kind (`step_kind_needed`) must name a step of that kind, or the golden must have one.

- [ ] **Step 1: Write the failing tests** (append to `tests/evals/harness/test_scenario.py`)

```python
import yaml

from evals.harness.scenario import AdvanceTriggerStep, DndStep, step_kind

EVENT = {"ha_event": {"entity_id": "light.bedroom_lamp", "state": "on"}}


def golden(steps: list[dict[str, Any]], expect: list[dict[str, Any]]) -> Scenario:
    return Scenario.model_validate(
        {"id": "reflex.t.t", "prd": ["x"], "status": "shipped", "steps": steps, "expect": expect}
    )


def test_the_new_steps_load_from_yaml() -> None:
    raw = yaml.safe_load(
        """
id: reflex.t.t
prd: [x]
status: shipped
steps:
  - clock: {hour: 22}
  - dnd: on
  - advance_trigger:
  - advance_trigger: {name: laundry}
    settle: 5
  - ha_event: {entity_id: light.bedroom_lamp, state: "on"}
expect:
  - reflex_decision: {decision: none}
"""
    )
    s = Scenario.model_validate(raw)
    kinds = [step_kind(step) for step in s.steps]
    assert kinds == ["clock", "dnd", "advance_trigger", "advance_trigger", "ha_event"]
    assert isinstance(s.steps[1], DndStep) and s.steps[1].dnd is True
    assert isinstance(s.steps[2], AdvanceTriggerStep) and s.steps[2].advance_trigger.name is None
    assert s.watches_reflex


@pytest.mark.parametrize(
    "step", [{"dnd": "on"}, {"clock": {"hour": 24}}, {"clock": {}}, {"advance_trigger": {"x": 1}}]
)
def test_malformed_new_steps_fail_to_load(step: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        golden([step], [{"ha_not_called": {}}])


def test_at_step_must_name_a_step_of_the_kind_the_check_reads() -> None:
    steps = [{"user": "a"}, EVENT]
    golden(steps, [{"reflex_decision": {"decision": "none", "at_step": 1}}])
    with pytest.raises(ValidationError, match="at_step 0 is a user step, not ha_event"):
        golden(steps, [{"reflex_decision": {"decision": "none", "at_step": 0}}])
    with pytest.raises(ValidationError, match="at_step is 2, but the golden has 2 steps"):
        golden(steps, [{"reflex_not_proposed": {"tool": "home.light_turn_on", "at_step": 2}}])
    with pytest.raises(ValidationError, match="needs a ha_event step"):
        golden([{"user": "a"}], [{"reflex_decision": {"decision": "none"}}])
    fire_ms = {"latency": {"metric": "reminder_fire_ms", "max": 5000}}
    with pytest.raises(ValidationError, match="needs a advance_trigger step"):
        golden([{"user": "a"}], [fire_ms])
    golden([{"user": "a"}, {"advance_trigger": {}}], [fire_ms])


def test_a_reflex_latency_golden_needs_no_user_step() -> None:
    s = golden([EVENT], [{"latency": {"metric": "reflex_ms", "max": 500}}])
    assert s.watches_reflex
    with pytest.raises(ValidationError, match="need at least one user step"):
        golden([EVENT], [{"latency": {"metric": "reply_ms", "max": 500}}])


def test_a_plain_golden_does_not_watch_reflex() -> None:
    assert not golden([{"user": "a"}], [{"reply_contains": {"text": "x"}}]).watches_reflex
```

(Add `from typing import Any` and `import pytest`/`ValidationError` imports if the file lacks them.)

- [ ] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_scenario.py -q`
Expected: FAIL with `ImportError: cannot import name 'AdvanceTriggerStep'`.

- [ ] **Step 3: Implement** (`evals/harness/scenario.py`)

Imports:

- add `StrictBool` to the pydantic import;
- import `CHECK_PARAMS`, `needs_reply`, `step_kind_needed` and `watches_reflex as _watches_reflex` from `evals.harness.checks`;
- import `STEP_KINDS` and `StepKind` from `evals.harness.evidence`.

Delete the local `_STEP_KINDS`. After `WaitStep`, add:

```python
class Clock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hour: int = Field(ge=0, le=23)  # the local hour Reflex's prompt shows from this step on


class ClockStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clock: Clock


class AdvanceTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None  # a substring of the trigger's name; None: the newest


class AdvanceTriggerStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    advance_trigger: AdvanceTrigger
    # How long to wait for the trigger to fire. None: the driver's default.
    settle: float | None = Field(default=None, ge=0)

    @field_validator("advance_trigger", mode="before")
    @classmethod
    def _bare_key_means_the_newest(cls, value: Any) -> Any:
        return {} if value is None else value


class DndStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # YAML reads a bare on/off as a boolean, so `dnd: on` is true; a quoted "on" is a mistake.
    dnd: StrictBool
    settle: float = Field(default=3.0, ge=0)
```

Rename `_step_kind` to `step_kind`, returning `StepKind | None`, and iterate `STEP_KINDS`:

```python
def step_kind(value: Any) -> StepKind | None:
    """The step's tag: the one step key a mapping (or a step model's fields) carries."""
    if isinstance(value, BaseModel):
        keys = set(type(value).model_fields)
    elif isinstance(value, dict):
        keys = set(value)
    else:
        return None
    kinds = [k for k in STEP_KINDS if k in keys]
    return kinds[0] if len(kinds) == 1 else None
```

Extend the `Step` union and its discriminator:

```python
Step = Annotated[
    Annotated[UserStep, Tag("user")]
    | Annotated[HaEventStep, Tag("ha_event")]
    | Annotated[WaitStep, Tag("wait")]
    | Annotated[ClockStep, Tag("clock")]
    | Annotated[AdvanceTriggerStep, Tag("advance_trigger")]
    | Annotated[DndStep, Tag("dnd")],
    Discriminator(
        step_kind,
        custom_error_type="step_kind",
        custom_error_message="needs one of " + ", ".join(STEP_KINDS),
    ),
]
```

In `Scenario`, add the property:

```python
    @property
    def watches_reflex(self) -> bool:
        """Whether a check reads System 1's calls: the driver then waits for them after an
        ha_event, and out the attention cooldown after a restore."""
        return any(_watches_reflex(c.name, c.params) for c in self.expect)
```

Replace the `after_step` loop at the end of `_coherent` with:

```python
        # after_step and at_step count every step; the driver reads after_step mid-play.
        n = len(self.steps)
        kinds = [step_kind(s) for s in self.steps]
        for i, check in enumerate(self.expect):
            for field in ("after_step", "at_step"):
                index = getattr(check.params, field, None)
                if isinstance(index, int) and not -n <= index < n:
                    raise ValueError(
                        f"expect.{i}.{field} is {index}, but the golden has {n} steps "
                        f"({-n} to {n - 1})"
                    )
            wanted = step_kind_needed(check.name, check.params)
            if wanted is None:
                continue
            at = getattr(check.params, "at_step", None)
            if at is None and wanted not in kinds:
                raise ValueError(f"expect.{i} ({check.name}) needs a {wanted} step")
            if at is not None and kinds[at] != wanted:
                raise ValueError(f"expect.{i}.at_step {at} is a {kinds[at]} step, not {wanted}")
        return self
```

Run `grep -rn "_step_kind" evals tests` and rename every hit to `step_kind`.

- [ ] **Step 4: Run the tests and watch them pass**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_scenario.py tests/evals/test_goldens_load.py tests/evals/harness/test_driver.py -q`
Expected: PASS. The driver's `match` still compiles because the new step kinds are not yet played: mypy's `assert_never` flags that, and Task 9 closes it. If mypy runs before Task 9, `assert_never` will error, which is expected.

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff check --fix evals/harness/scenario.py tests/evals/harness/test_scenario.py
.venv/bin/ruff format evals/harness/scenario.py tests/evals/harness/test_scenario.py
git add evals/harness/scenario.py tests/evals/harness/test_scenario.py
git commit -m "feat(evals): clock, advance_trigger and dnd steps; at_step names a step of its kind"
```

---

### Task 6: People, the sun and rooms in the world

Reflex's prompt shows who is home and whether the sun is up (`render_now`), so the world needs people and `sun.sun`. The `prompt_is_compact` golden needs something to keep out of Reflex's prompt:

- a `button.*` entity;
- an `entity_picture` attribute;
- a `supported_features` attribute.

The reflex checks resolve a proposal's target to rooms, which needs area lookups.

**Files:**
- Modify:
  - `evals/harness/world.py`
  - `evals/harness/worlds/apartment.yaml`
- Test: `tests/evals/harness/test_world.py`

**Interfaces:**
- Produces:
  - `World.area_of(entity_id) -> str | None`: the entity's area, else its device's area
  - `World.entities_in(area_id, domain=None) -> list[str]`: enabled entities only
  - new world entities: `person.alex`, `person.sam`, `sun.sun`, `button.router_restart`

- [ ] **Step 1: Write the failing tests** (append to `tests/evals/harness/test_world.py`)

```python
def test_apartment_has_two_people_home_and_the_sun_up() -> None:
    states = load_world("apartment").initial_states()
    assert states["person.alex"].state == "home" and states["person.sam"].state == "home"
    assert states["sun.sun"].state == "above_horizon"
    assert "entity_picture" in states["person.alex"].attributes
    assert "supported_features" in states["light.living_room_lamp"].attributes
    assert "button.router_restart" in states


def test_area_of_uses_the_entity_then_its_device() -> None:
    world = load_world("apartment")
    assert world.area_of("light.bedroom_lamp") == "bedroom"
    assert world.area_of("media_player.living_room_tv") == "living_room"
    assert world.area_of("person.alex") is None
    assert world.area_of("light.not_there") is None
    by_device = World.model_validate(
        {
            "name": "t",
            "areas": [{"area_id": "den", "name": "Den"}],
            "devices": [{"id": "d1", "name": "Lamp", "area_id": "den"}],
            "entities": [
                {"entity_id": "light.den", "name": "Den", "device_id": "d1", "state": "off"}
            ],
            "services": {"light": {}},
        }
    )
    assert by_device.area_of("light.den") == "den"


def test_entities_in_an_area_by_domain_skip_disabled_ones() -> None:
    world = load_world("apartment")
    assert world.entities_in("living_room", "light") == [
        "light.living_room_lamp",
        "light.living_room_ceiling",
    ]
    assert "switch.coffee_maker" in world.entities_in("kitchen")
    assert "light.hallway_old" not in world.entities_in("entryway")  # disabled
```

(Import `World` from `evals.harness.world` if the file does not already. If `World` validation needs fields the minimal world above lacks, add them with empty values: the test is about `area_of`, not validation.)

- [ ] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_world.py -q`
Expected: FAIL with `KeyError: 'person.alex'` and `AttributeError: 'World' object has no attribute 'area_of'`.

- [ ] **Step 3: Implement**

In `evals/harness/worlds/apartment.yaml`:

- give `light.living_room_lamp` the attributes `{brightness: 180, supported_features: 44}`;
- append, after the last entity:

```yaml
  - {entity_id: person.alex, name: Alex, state: home, attributes: {entity_picture: /api/image/serve/alex/512x512}}
  - {entity_id: person.sam, name: Sam, state: home}
  - {entity_id: sun.sun, name: Sun, state: above_horizon}
  - {entity_id: button.router_restart, name: Router Restart, state: unknown}
```

People, the sun and the button have no area, so slice 1's room goldens (`discovery_room_inventory`, `which_lights_on`) see the same rooms as before.

In `evals/harness/world.py`, replace `World.area_of`, which today reads only the entity's own `area_id` and has no callers, and add `entities_in` after it:

```python
    def area_of(self, entity_id: str) -> str | None:
        """The entity's room: its own area, else its device's."""
        entity = next((e for e in self.entities if e.entity_id == entity_id), None)
        if entity is None:
            return None
        if entity.area_id is not None:
            return entity.area_id
        device = next((d for d in self.devices if d.id == entity.device_id), None)
        return None if device is None else device.area_id

    def entities_in(self, area_id: str, domain: str | None = None) -> list[str]:
        """Enabled entities in the area, in world order, optionally of one domain."""
        return [
            e.entity_id
            for e in self.entities
            if not e.disabled
            and self.area_of(e.entity_id) == area_id
            and (domain is None or e.entity_id.split(".", 1)[0] == domain)
        ]
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_world.py tests/evals/harness/test_fake_ha.py tests/evals/test_goldens_load.py -q`
Expected: PASS. The fake HA serves the new entities. A person, the sun and the button have no services, so home-service generates no tools for them.

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff check --fix evals/harness/world.py tests/evals/harness/test_world.py
.venv/bin/ruff format evals/harness/world.py tests/evals/harness/test_world.py
git add evals/harness/world.py evals/harness/worlds/apartment.yaml tests/evals/harness/test_world.py
git commit -m "feat(evals): people, the sun and room lookups in the apartment world"
```

---
### Task 7: The bus: what the driver reads from the container and does through it

`ContainerBus` is the only code that knows Alfred's Redis keys and the admin API. It returns raw entries; parsing them into evidence is Task 8's job. It reads the stack's Redis client lazily, so a stack restart (a new client) needs nothing re-wired.

**Files:**
- Create: `evals/harness/bus.py`
- Modify:
  - `evals/harness/stack.py`: `Stack.bus`
  - `pyproject.toml` and `uv.lock`: `fakeredis` in the `dev` extra
- Test:
  - `tests/evals/harness/test_bus.py` (new)
  - `tests/evals/harness/test_stack.py`

**Interfaces:**
- Produces, in `evals/harness/bus.py`:
  - `BusError(RuntimeError)`
  - `Entry(wall: float, data: dict[str, str])`, a frozen dataclass; `wall` is seconds since the epoch
  - `Bus`, a `Protocol` with these methods:
    - `events(since_wall) -> list[Entry]`: `alfred:events`
    - `notifications(since_wall) -> list[Entry]`: the dispatch stream
    - `deferred() -> list[str]`
    - `reflex_tools() -> list[ToolInfo]`
    - `advance_trigger(trigger_id, now: datetime) -> bool`
    - `delete_triggers(trigger_ids: list[str]) -> None`
    - `user_timezone() -> str | None`: the stored value, None when none is stored
    - `set_user_timezone(tz: str | None) -> None`: None deletes the stored value
    - `set_dnd(active: bool) -> None`
    - `clear_dnd() -> None`
  - `ContainerBus(redis: Callable[[], AioRedis], web_url: Callable[[], str], *, http: Callable[[], httpx.AsyncClient] = httpx.AsyncClient, timeout_s: float = 10.0)`, which implements `Bus`
  - `zone_for_hour(hour: int, now: datetime) -> str`
- Produces, in `evals/harness/stack.py`: `Stack.bus: ContainerBus`. Before `start()`, every call raises `StackError("… is not started")`.

- [ ] **Step 1: Add the dependency**

Run: `uv add --optional dev fakeredis`
Expected: `pyproject.toml` lists `fakeredis` under `[project.optional-dependencies] dev`, and `uv.lock` changes. Then `.venv/bin/python -c "import fakeredis; print(fakeredis.__version__)"` prints a version.

- [ ] **Step 2: Write the failing tests** (`tests/evals/harness/test_bus.py`)

```python
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import fakeredis
import httpx
import pytest

from core.reflex.tool_registry import ToolRegistry
from evals.harness.bus import BusError, ContainerBus, zone_for_hour
from shared.streams import (
    AUTH_SESSION_PREFIX,
    DEFERRED_NOTIFICATIONS_KEY,
    DND_STATE_KEY,
    EVENTS_STREAM,
    NOTIFICATION_DISPATCH_STREAM,
    TRIGGERS_CHANGED_CHANNEL,
    TRIGGERS_KEY,
    USER_TIMEZONE_KEY,
)

Handler = Callable[[httpx.Request], Awaitable[httpx.Response]]


def at(hour: int, minute: int = 30) -> datetime:
    return datetime(2026, 10, 8, hour, minute, tzinfo=UTC)


@pytest.mark.parametrize(
    ("hour", "utc_hour", "zone"),
    [
        (22, 15, "Etc/GMT-7"),
        (2, 20, "Etc/GMT-6"),
        (3, 12, "Etc/GMT+9"),
        (15, 15, "Etc/GMT"),
        (10, 20, "Etc/GMT-14"),
    ],
)
def test_zone_for_hour(hour: int, utc_hour: int, zone: str) -> None:
    assert zone_for_hour(hour, at(utc_hour)) == zone


def test_every_hour_has_a_zone_that_shows_it_at_any_utc_hour() -> None:
    for utc_hour in range(24):
        now = at(utc_hour, 57)
        for hour in range(24):
            assert now.astimezone(ZoneInfo(zone_for_hour(hour, now))).hour == hour


@pytest.fixture
def redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis()


async def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={})


def bus(redis: fakeredis.FakeAsyncRedis, handler: Handler = ok) -> ContainerBus:
    transport = httpx.MockTransport(handler)
    return ContainerBus(
        lambda: redis,
        lambda: "http://alfred.test",
        http=lambda: httpx.AsyncClient(transport=transport),
    )


async def published(pubsub: Any, tries: int = 5) -> list[dict[str, str]]:
    """Every message on the subscription. ``get_message`` returns None for the subscribe
    confirmation too, so one None does not mean the channel is quiet."""
    out: list[dict[str, str]] = []
    for _ in range(tries):
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
        if message is not None:
            out.append(json.loads(message["data"]))
    return out


async def test_streams_are_read_from_a_wall_time(redis: fakeredis.FakeAsyncRedis) -> None:
    await redis.xadd(EVENTS_STREAM, {"event": "old"}, id="1000-0")
    await redis.xadd(EVENTS_STREAM, {"event": "new"}, id="5000-0")
    await redis.xadd(NOTIFICATION_DISPATCH_STREAM, {"notification": "n"}, id="6000-0")
    await redis.rpush(DEFERRED_NOTIFICATIONS_KEY, "a", "b")
    b = bus(redis)
    assert [(e.wall, e.data) for e in await b.events(2.0)] == [(5.0, {"event": "new"})]
    assert [e.wall for e in await b.notifications(0.0)] == [6.0]
    assert await b.deferred() == ["a", "b"]


async def test_advance_pulls_run_at_to_now_and_tells_the_engine(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    run_at = "2026-10-08T20:00:00-06:00"
    row = {"trigger_id": "t1", "name": "Laundry", "conditions": {"run_at": run_at}}
    await redis.hset(TRIGGERS_KEY, "t1", json.dumps(row))
    await redis.hset(TRIGGERS_KEY, "cron", json.dumps({"conditions": {"cron": "0 7 * * *"}}))
    pubsub = redis.pubsub()
    await pubsub.subscribe(TRIGGERS_CHANGED_CHANNEL)
    now = at(18)
    b = bus(redis)
    assert await b.advance_trigger("t1", now)
    stored = json.loads(await redis.hget(TRIGGERS_KEY, "t1"))
    assert stored["conditions"]["run_at"] == now.isoformat() and stored["name"] == "Laundry"
    assert not await b.advance_trigger("cron", now)  # no run_at to bring forward
    assert not await b.advance_trigger("gone", now)
    assert await published(pubsub) == [{"op": "saved", "trigger_id": "t1"}]
    await pubsub.aclose()


async def test_delete_triggers_removes_only_rows_that_exist_and_says_so(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    await redis.hset(TRIGGERS_KEY, mapping={"t1": "{}", "keep": "{}"})
    pubsub = redis.pubsub()
    await pubsub.subscribe(TRIGGERS_CHANGED_CHANNEL)
    await bus(redis).delete_triggers(["t1", "already-fired"])
    assert await redis.hkeys(TRIGGERS_KEY) == [b"keep"]
    assert await published(pubsub) == [{"op": "deleted", "trigger_id": "t1"}]
    await pubsub.aclose()


async def test_user_timezone_is_set_restored_and_validated(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    b = bus(redis)
    assert await b.user_timezone() is None
    await b.set_user_timezone("Etc/GMT-7")
    assert await b.user_timezone() == "Etc/GMT-7"
    await b.set_user_timezone(None)
    assert await redis.get(USER_TIMEZONE_KEY) is None
    with pytest.raises(BusError, match="Mars/Base"):
        await b.set_user_timezone("Mars/Base")


async def test_set_dnd_posts_with_a_session_that_lives_only_for_the_call(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    seen: list[tuple[str, object, bytes | None]] = []

    async def admin(request: httpx.Request) -> httpx.Response:
        session_id = request.headers["cookie"].removeprefix("alfred_auth=")
        session = await redis.hgetall(f"{AUTH_SESSION_PREFIX}{session_id}")
        seen.append((str(request.url), json.loads(request.content), session.get(b"authenticated")))
        return httpx.Response(200, json={"active": True})

    await bus(redis, admin).set_dnd(True)
    assert seen == [("http://alfred.test/api/admin/dnd", {"active": True}, b"1")]
    assert await redis.keys(f"{AUTH_SESSION_PREFIX}*") == []


async def test_a_refused_dnd_call_is_a_bus_error_and_leaves_no_session(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    async def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "Not authenticated"})

    with pytest.raises(BusError, match="401"):
        await bus(redis, refuse).set_dnd(True)
    assert await redis.keys(f"{AUTH_SESSION_PREFIX}*") == []


async def test_clear_dnd_drops_the_state_and_what_it_held(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    await redis.set(DND_STATE_KEY, json.dumps({"active": True}))
    await redis.rpush(DEFERRED_NOTIFICATIONS_KEY, "held")
    await bus(redis).clear_dnd()
    assert await redis.exists(DND_STATE_KEY, DEFERRED_NOTIFICATIONS_KEY) == 0


async def test_reflex_tools_are_the_registrys_reflex_audience(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    manifest = {
        "features": [
            {
                "name": "home",
                "tools": [
                    {"name": "home.light_turn_on", "audience": "reflex"},
                    {"name": "home.lock_unlock"},
                ],
            }
        ]
    }
    await redis.hset(ToolRegistry.REGISTRY_KEY, "home-service", json.dumps(manifest))
    assert [t.name for t in await bus(redis).reflex_tools()] == ["home.light_turn_on"]
```

Append to `tests/evals/harness/test_stack.py`. Add `import fakeredis` and `from shared.streams import USER_TIMEZONE_KEY` to the imports if they are missing.

```python
async def test_the_bus_follows_the_stacks_redis(tmp_path: Path) -> None:
    stack = make_stack(tmp_path, FakeDocker())
    with pytest.raises(StackError, match="not started"):
        await stack.bus.user_timezone()
    stack.redis = fakeredis.FakeAsyncRedis()
    stack.web_port = 1234
    await stack.redis.set(USER_TIMEZONE_KEY, "Etc/GMT-7")
    assert await stack.bus.user_timezone() == "Etc/GMT-7"
    assert stack._web_url() == "http://127.0.0.1:1234"
```

- [ ] **Step 3: Run the tests and watch them fail**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_bus.py tests/evals/harness/test_stack.py -q`
Expected: FAIL. `test_bus.py` errors with `No module named 'evals.harness.bus'`, and the stack test with `AttributeError: 'Stack' object has no attribute 'bus'`.

- [ ] **Step 4: Create `evals/harness/bus.py`**

```python
"""The eval container's bus: what the driver reads from Alfred and does through it.

Reads:
- ``alfred:events`` (TriggerCreated and TriggerFired);
- the notification dispatch stream;
- the deferred list;
- the registry's reflex-audience tools, which are what Reflex's prompt shows;
- the stored user timezone.

Acts:
- pulls a time trigger's ``run_at`` to now;
- deletes triggers;
- sets the user timezone, which is Reflex's clock;
- sets do-not-disturb through the admin API, as the PWA does;
- clears do-not-disturb.

Entries come back raw; ``collect.py`` parses them. The Redis client is read through a
callable, so a stack restart's new client needs nothing re-wired.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

import httpx

from core.identity.auth_middleware import COOKIE_NAME
from core.reflex.tool_registry import ToolRegistry
from shared import usertime
from shared.streams import (
    AUTH_SESSION_PREFIX,
    DEFERRED_NOTIFICATIONS_KEY,
    DND_STATE_KEY,
    EVENTS_STREAM,
    NOTIFICATION_DISPATCH_STREAM,
    TRIGGER_SYNC_OP_DELETED,
    TRIGGER_SYNC_OP_SAVED,
    TRIGGER_SYNC_OP_TZ_CHANGED,
    TRIGGERS_CHANGED_CHANNEL,
    TRIGGERS_KEY,
    USER_TIMEZONE_KEY,
    decode_stream_value,
)

if TYPE_CHECKING:
    from core.reflex.tool_registry import ToolInfo
    from shared.types import AioRedis

REFLEX_AUDIENCE = "reflex"
EVAL_CREDENTIAL = "alfred-eval"  # the credential_id on the harness's admin sessions
_SESSION_TTL_S = 300  # a backstop: the session is deleted right after its one call


class BusError(RuntimeError):
    """The bus could not do what the driver asked. The harness failed, not Alfred."""


@dataclass(frozen=True)
class Entry:
    wall: float  # seconds since the epoch, from the stream entry's id
    data: dict[str, str]


class Bus(Protocol):
    async def events(self, since_wall: float) -> list[Entry]: ...
    async def notifications(self, since_wall: float) -> list[Entry]: ...
    async def deferred(self) -> list[str]: ...
    async def reflex_tools(self) -> list[ToolInfo]: ...
    async def advance_trigger(self, trigger_id: str, now: datetime) -> bool: ...
    async def delete_triggers(self, trigger_ids: list[str]) -> None: ...
    async def user_timezone(self) -> str | None: ...
    async def set_user_timezone(self, tz: str | None) -> None: ...
    async def set_dnd(self, active: bool) -> None: ...
    async def clear_dnd(self) -> None: ...


def zone_for_hour(hour: int, now: datetime) -> str:
    """The ``Etc/GMT`` zone whose local hour is *hour* at *now*.

    The offset is taken in -9..+14 hours, so every hour has exactly one zone, and every
    such zone exists (they run from UTC-12 to UTC+14). ``Etc/GMT`` signs are inverted:
    ``Etc/GMT-8`` is UTC+8.
    """
    offset = (hour - now.astimezone(UTC).hour) % 24
    if offset > 14:
        offset -= 24
    return "Etc/GMT" if offset == 0 else f"Etc/GMT{-offset:+d}"


class ContainerBus:
    def __init__(
        self,
        redis: Callable[[], AioRedis],
        web_url: Callable[[], str],
        *,
        http: Callable[[], httpx.AsyncClient] = httpx.AsyncClient,
        timeout_s: float = 10.0,
    ) -> None:
        self._redis = redis
        self._web_url = web_url
        self._http = http
        self._timeout_s = timeout_s

    async def _range(self, stream: str, since_wall: float) -> list[Entry]:
        raw: list[tuple[Any, dict[Any, Any]]] = await self._redis().xrange(
            stream, min=str(int(since_wall * 1000)), max="+"
        )
        entries: list[Entry] = []
        for entry_id, fields in raw:
            ms = int(decode_stream_value(entry_id).split("-", 1)[0])
            data = {decode_stream_value(k): decode_stream_value(v) for k, v in fields.items()}
            entries.append(Entry(wall=ms / 1000, data=data))
        return entries

    async def events(self, since_wall: float) -> list[Entry]:
        return await self._range(EVENTS_STREAM, since_wall)

    async def notifications(self, since_wall: float) -> list[Entry]:
        return await self._range(NOTIFICATION_DISPATCH_STREAM, since_wall)

    async def deferred(self) -> list[str]:
        raw: list[Any] = await self._redis().lrange(DEFERRED_NOTIFICATIONS_KEY, 0, -1)
        return [decode_stream_value(r) for r in raw]

    async def reflex_tools(self) -> list[ToolInfo]:
        tools = await ToolRegistry(self._redis()).get_tools()
        return [t for t in tools if t.audience == REFLEX_AUDIENCE]

    async def _publish(self, payload: dict[str, str]) -> None:
        await self._redis().publish(TRIGGERS_CHANGED_CHANNEL, json.dumps(payload))

    async def advance_trigger(self, trigger_id: str, now: datetime) -> bool:
        """Make a stored time trigger due at *now*, and tell the engine the way
        ``TriggerStore.save`` does. Its scheduler wakes on the message and fires the
        trigger. False when the row is gone (a one-shot that already fired) or has no
        ``run_at`` (a cron trigger)."""
        r = self._redis()
        raw = await r.hget(TRIGGERS_KEY, trigger_id)
        if raw is None:
            return False
        row: dict[str, Any] = json.loads(decode_stream_value(raw))
        conditions = row.get("conditions")
        if not isinstance(conditions, dict) or conditions.get("run_at") is None:
            return False
        conditions["run_at"] = now.isoformat()
        await r.hset(TRIGGERS_KEY, trigger_id, json.dumps(row))
        await self._publish({"op": TRIGGER_SYNC_OP_SAVED, "trigger_id": trigger_id})
        return True

    async def delete_triggers(self, trigger_ids: list[str]) -> None:
        """Remove triggers as ``TriggerStore.delete`` does, except for its YAML snapshot:
        the container's data dir is wiped with the container."""
        r = self._redis()
        for trigger_id in trigger_ids:
            if await r.hdel(TRIGGERS_KEY, trigger_id):
                await self._publish({"op": TRIGGER_SYNC_OP_DELETED, "trigger_id": trigger_id})

    async def user_timezone(self) -> str | None:
        """The stored zone. None when nothing is stored: Alfred then falls back to its env."""
        raw = await self._redis().get(USER_TIMEZONE_KEY)
        return None if raw is None else decode_stream_value(raw)

    async def set_user_timezone(self, tz: str | None) -> None:
        r = self._redis()
        if tz is None:
            if await r.delete(USER_TIMEZONE_KEY):
                await self._publish({"op": TRIGGER_SYNC_OP_TZ_CHANGED})
            return
        # usertime.set_user_timezone returns False for an unchanged zone too, so validate
        # here rather than read its result.
        if not usertime.is_valid_timezone(tz):
            raise BusError(f"{tz!r} is not a time zone the harness's Python knows")
        await usertime.set_user_timezone(r, tz)

    async def set_dnd(self, active: bool) -> None:
        """Turn do-not-disturb on or off through the admin API, as the PWA does. Turning it
        off queues the drain of held notifications, which deleting the key would not. The
        admin session is minted for this one call and deleted after it."""
        r = self._redis()
        session_id = uuid4().hex
        key = f"{AUTH_SESSION_PREFIX}{session_id}"
        await r.hset(
            key,
            mapping={
                "authenticated": "1",
                "credential_id": EVAL_CREDENTIAL,
                "created_at": datetime.now(UTC).isoformat(),
                "channel": "eval",
            },
        )
        await r.expire(key, _SESSION_TTL_S)
        try:
            async with self._http() as client:
                response = await client.post(
                    f"{self._web_url()}/api/admin/dnd",
                    json={"active": active},
                    headers={"Cookie": f"{COOKIE_NAME}={session_id}"},
                    timeout=self._timeout_s,
                )
        except httpx.HTTPError as exc:
            raise BusError(f"POST /api/admin/dnd failed: {exc}") from exc
        finally:
            await r.delete(key)
        if not response.is_success:
            raise BusError(
                f"POST /api/admin/dnd answered {response.status_code}: {response.text[:200]}"
            )

    async def clear_dnd(self) -> None:
        """Drop do-not-disturb and what it held, without a drain: a drain now would deliver
        this sample's held notifications in the next sample's window."""
        await self._redis().delete(DND_STATE_KEY, DEFERRED_NOTIFICATIONS_KEY)
```

If mypy flags a redis call's return type, follow the `# type: ignore[misc,unused-ignore]` pattern in `shared/usertime.py`.

- [ ] **Step 5: Give the stack its bus** (`evals/harness/stack.py`)

Import `ContainerBus` from `evals.harness.bus`. At the end of `Stack.__init__`, add:

```python
        # Reads self.redis and self.web_port on every call, so a restart's new client
        # and port need nothing re-wired.
        self.bus = ContainerBus(self._live_redis, self._web_url)
```

After `__init__`, add:

```python
    def _live_redis(self) -> AioRedis:
        if self.redis is None:
            raise StackError(f"{self.name} is not started")
        return self.redis

    def _web_url(self) -> str:
        if self.web_port is None:
            raise StackError(f"{self.name} is not started")
        return f"http://127.0.0.1:{self.web_port}"
```

- [ ] **Step 6: Run the tests and watch them pass**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_bus.py tests/evals/harness/test_stack.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
.venv/bin/ruff check --fix evals/harness/bus.py evals/harness/stack.py tests/evals/harness
.venv/bin/ruff format evals/harness/bus.py evals/harness/stack.py tests/evals/harness
.venv/bin/mypy evals/harness/bus.py evals/harness/stack.py
git add evals/harness/bus.py evals/harness/stack.py tests/evals/harness/test_bus.py tests/evals/harness/test_stack.py pyproject.toml uv.lock
git commit -m "feat(evals): a bus to the eval container's triggers, notifications, DND and clock"
```

---

### Task 8: Bus entries and System 1 calls become evidence

**Files:**
- Create:
  - `evals/harness/collect.py`
  - `evals/harness/reflex.py`
- Test: `tests/evals/harness/test_collect.py` (new)

**Interfaces:**
- Consumes:
  - from Task 7: `Entry`;
  - from Task 2: `TriggerRecord`, `TriggerFire`, `NotificationRecord`, `ReflexCall`;
  - from Task 3: `message_text`;
  - from Task 6: `World.area_of` and `World.entities_in`.
- Produces, in `evals/harness/collect.py`:
  - `TOOL_CALL = "tool-call"`
  - `trigger_records(entries, started, started_wall) -> tuple[list[TriggerRecord], list[TriggerFire]]`
  - `notification_records(entries, started, started_wall) -> list[NotificationRecord]`
  - `deferred_records(raws: list[str]) -> list[NotificationRecord]`

  `started` is the sample's monotonic start, and `started_wall` is `time.time()` at the same instant. A record's `t` is on the sample's monotonic clock.
- Produces, in `evals/harness/reflex.py`:
  - `local_hour(messages) -> int | None`
  - `tool_domain(tool, domains) -> str | None`
  - `targets(world, tool, parameters) -> list[str]`
  - `reflex_calls(llm_calls, tools, world) -> list[ReflexCall]`

- [ ] **Step 1: Write the failing tests** (`tests/evals/harness/test_collect.py`)

```python
from __future__ import annotations

import json
from datetime import UTC, datetime

from bus.schemas.events import TriggerCreated, TriggerFired
from core.notifications.schema import Notification, Urgency
from core.reflex.tool_registry import ToolInfo
from evals.harness.bus import Entry
from evals.harness.collect import deferred_records, notification_records, trigger_records
from evals.harness.evidence import LlmCall
from evals.harness.reflex import local_hour, reflex_calls, targets, tool_domain
from evals.harness.world import load_world

WALL0 = 1_760_000_000.0  # time.time() when the sample started
STARTED = 50.0  # time.monotonic() at the same instant
CREATED = datetime(2026, 10, 8, 18, 0, tzinfo=UTC)


def event_entry(event: TriggerCreated | TriggerFired, wall: float) -> Entry:
    return Entry(wall=wall, data={"event": event.model_dump_json()})


def made(created_by: str = "tool-call") -> TriggerCreated:
    return TriggerCreated(
        trigger_id="t1",
        trigger_type="time",
        name="Laundry",
        created_by=created_by,
        conditions={"run_at": "2026-10-08T12:20:00-06:00"},
        urgency="urgent",
        timestamp=CREATED,
    )


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
    held = deferred_records([note.model_dump_json(), "{broken"])
    assert [(n.t, n.title) for n in held] == [(None, "Trigger: Vet")]


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


REFLEX_TOOL = ToolInfo(
    name="home.light_turn_off",
    description="",
    parameters={"target": {}},
    feature_name="home",
    feature_description="",
    target_service="home-service",
    audience="reflex",
)


def s1(text: str | None, status: int = 200, role: str = "system1") -> LlmCall:
    return LlmCall(
        t=3.0,
        role=role,  # type: ignore[arg-type]
        latency_ms=700.0,
        status=status,
        messages=prompt("Thu 8 Oct, 22:05 (night)"),
        response_text=text,
    )


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
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_collect.py -q`
Expected: FAIL with `No module named 'evals.harness.collect'`.

- [ ] **Step 3: Create `evals/harness/collect.py`**

```python
"""Bus entries become evidence records, timed on the sample's monotonic clock.

A stream entry's id is its wall time in ms. ``started``/``started_wall`` are the same
instant on both clocks, so ``t = started + (wall - started_wall)``. The container shares
the host's kernel clock, so the two agree.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from bus.schemas.events import TriggerCreated, TriggerFired
from core.notifications.schema import Notification
from evals.harness.evidence import NotificationRecord, TriggerFire, TriggerRecord

if TYPE_CHECKING:
    from evals.harness.bus import Entry

logger = logging.getLogger(__name__)

TOOL_CALL = "tool-call"  # TriggerCreated.created_by when System 2 used its tool


def _on_sample_clock(wall: float, started: float, started_wall: float) -> float:
    return started + (wall - started_wall)


def _event(entry: Entry) -> dict[str, Any] | None:
    raw = entry.data.get("event")
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Unreadable entry on alfred:events: %r", raw[:120])
        return None
    return parsed if isinstance(parsed, dict) else None


def trigger_records(
    entries: list[Entry], started: float, started_wall: float
) -> tuple[list[TriggerRecord], list[TriggerFire]]:
    """The triggers System 2 created (other creators, such as the notification
    dispatcher's drain trigger, are not its behaviour), and every fire."""
    created: list[TriggerRecord] = []
    fired: list[TriggerFire] = []
    for entry in entries:
        event = _event(entry)
        if event is None:
            continue
        t = _on_sample_clock(entry.wall, started, started_wall)
        kind = event.get("event_type")
        try:
            if kind == "trigger_created":
                made = TriggerCreated.model_validate(event)
                if made.created_by == TOOL_CALL:
                    created.append(
                        TriggerRecord(
                            t=t,
                            trigger_id=made.trigger_id,
                            trigger_type=made.trigger_type,
                            name=made.name,
                            created_by=made.created_by,
                            conditions=made.conditions,
                            urgency=made.urgency,
                            one_shot=made.one_shot,
                            created_at=made.timestamp,
                        )
                    )
            elif kind == "trigger_fired":
                fire = TriggerFired.model_validate(event)
                fired.append(
                    TriggerFire(
                        t=t,
                        trigger_id=fire.trigger_id,
                        name=fire.trigger_name,
                        trigger_type=fire.trigger_type,
                        urgency=fire.urgency,
                        fired_by=fire.fired_by,
                    )
                )
        except ValidationError as exc:
            logger.warning("Unreadable %s on alfred:events: %s", kind, exc)
    return created, fired


def _notification(raw: str) -> Notification | None:
    try:
        return Notification.model_validate_json(raw)
    except ValidationError as exc:
        logger.warning("Unreadable notification: %s", exc)
        return None


def _record(n: Notification, t: float | None) -> NotificationRecord:
    return NotificationRecord(
        t=t, title=n.title, body=n.body, urgency=str(n.urgency), source=n.source
    )


def notification_records(
    entries: list[Entry], started: float, started_wall: float
) -> list[NotificationRecord]:
    records: list[NotificationRecord] = []
    for entry in entries:
        raw = entry.data.get("notification")
        if raw is not None and (n := _notification(raw)) is not None:
            records.append(_record(n, _on_sample_clock(entry.wall, started, started_wall)))
    return records


def deferred_records(raws: list[str]) -> list[NotificationRecord]:
    return [_record(n, None) for raw in raws if (n := _notification(raw)) is not None]
```

- [ ] **Step 4: Create `evals/harness/reflex.py`**

```python
"""System 1's recorded calls become Reflex's decisions, parsed the way Reflex parses them.

Reflex sends the model's reply straight to ``core.reflex.decision.parse_decision``, along
with the reflex-audience tools its prompt showed (``core/reflex/engine.py``). The harness
does the same with the same tools. So an act naming a tool Reflex could not use is
``invalid`` here too.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from core.reflex.decision import parse_decision
from evals.harness.checks.llm import message_text
from evals.harness.evidence import ReflexCall

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from core.reflex.tool_registry import ToolInfo
    from evals.harness.evidence import LlmCall
    from evals.harness.world import World

# core/reflex/prompt.render_now's clock line: "Thu 8 Oct, 22:05 (night)".
_CLOCK = re.compile(
    r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{1,2} [A-Z][a-z]{2}, (\d{2}):\d{2} "
    r"\((?:morning|afternoon|evening|night)\)"
)


def local_hour(messages: list[dict[str, Any]]) -> int | None:
    """The hour Reflex's prompt showed, or None if it had no clock line."""
    for message in messages:
        if (m := _CLOCK.search(message_text(message))) is not None:
            return int(m.group(1))
    return None


def tool_domain(tool: str, domains: Iterable[str]) -> str | None:
    """The HA domain a generated home tool acts on: ``home.light_turn_off`` is ``light``.
    The longest prefix wins, so ``media_player_media_pause`` is ``media_player``."""
    name = tool.removeprefix("home.")
    return max((d for d in domains if name.startswith(f"{d}_")), key=len, default=None)


def targets(world: World, tool: str | None, parameters: dict[str, Any]) -> list[str]:
    """What a proposal would act on, as entity and area ids.

    The ``target`` parameter is resolved the way home-service resolves it
    (``EntityIndex.resolve``), within the tool's domain:
    1. an entity_id;
    2. else an area name;
    3. else an entity's friendly name.

    Each entity's area is added, so a golden can name either the room or the entity.
    """
    raw = parameters.get("target")
    domain = None if tool is None else tool_domain(tool, world.services)
    if domain is None or not isinstance(raw, str) or not raw.strip():
        return []
    wanted = raw.strip().casefold()
    candidates = [e for e in world.entities if not e.disabled and e.domain == domain]
    rooms = [a.area_id for a in world.areas if a.name.casefold() == wanted]
    hits = (
        [e.entity_id for e in candidates if e.entity_id.casefold() == wanted]
        or [entity for room in rooms for entity in world.entities_in(room, domain)]
        or [e.entity_id for e in candidates if e.name.casefold() == wanted]
    )
    out: list[str] = []
    for entity_id in hits:
        for item in (entity_id, world.area_of(entity_id)):
            if item is not None and item not in out:
                out.append(item)
    return out


def reflex_calls(
    llm_calls: list[LlmCall], tools: Sequence[ToolInfo], world: World
) -> list[ReflexCall]:
    """Every System 1 call, as the decision Reflex took from it."""
    out: list[ReflexCall] = []
    for call in llm_calls:
        if call.role != "system1":
            continue
        hour = local_hour(call.messages)
        if not 200 <= call.status < 300:
            out.append(
                ReflexCall(
                    t=call.t,
                    latency_ms=call.latency_ms,
                    decision="invalid",
                    problem=f"no reply (HTTP {call.status})",
                    local_hour=hour,
                )
            )
            continue
        proposal = parse_decision(call.response_text or "", tools)
        action = proposal.action
        tool = None if action is None else action.tool_name
        parameters = {} if action is None else dict(action.parameters)
        out.append(
            ReflexCall(
                t=call.t,
                latency_ms=call.latency_ms,
                decision=proposal.decision,
                reason=proposal.reason or "",
                tool=tool,
                parameters=parameters,
                targets=targets(world, tool, parameters),
                problem=proposal.problem,
                local_hour=hour,
            )
        )
    return out
```

- [ ] **Step 5: Run the tests and watch them pass**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_collect.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
.venv/bin/ruff check --fix evals/harness/collect.py evals/harness/reflex.py tests/evals/harness/test_collect.py
.venv/bin/ruff format evals/harness/collect.py evals/harness/reflex.py tests/evals/harness/test_collect.py
git add evals/harness/collect.py evals/harness/reflex.py tests/evals/harness/test_collect.py
git commit -m "feat(evals): parse bus entries and System 1 replies into evidence"
```

---

### Task 9: The driver plays the new steps, waits for Reflex, collects and cleans up

**Files:**
- Modify:
  - `evals/harness/driver.py`
  - `evals/harness/orchestrate.py`: `make_ctx` passes `bus=stack.bus`
  - `tests/evals/harness/factories.py`: `FakeBus`
  - `tests/evals/harness/test_driver.py`
  - `tests/evals/harness/test_tasks.py`: `context()` gets a bus
- Test: `tests/evals/harness/test_driver.py`

**Interfaces:**
- Consumes:
  - from Task 7: `Bus`, `zone_for_hour`, `Stack.bus`;
  - from Task 8: `trigger_records`, `notification_records`, `deferred_records`, `reflex_calls`;
  - from Task 5: `ClockStep`, `AdvanceTriggerStep`, `DndStep`, `step_kind`, `Scenario.watches_reflex`;
  - from Task 2: the `Evidence` fields.
- Produces:
  - `PlayContext.bus: Bus`: required, placed right after `proxy`
  - New `PlayContext` fields, with defaults:
    - `reflex_timeout_s=15.0`
    - `reflex_cooldown_s=6.0`
    - `fire_timeout_s=15.0`
    - `poll_s=0.2`
    - `now: Callable[[], datetime]` (UTC now)
  - `clock_wait_s(now: datetime) -> float`
  - `FakeBus`, in `tests/evals/harness/factories.py`

**Behaviour, in play order:**

1. **Restore and capture.**
   - Restore the world. A golden that watches Reflex then sleeps `max(restore_settle_s, reflex_cooldown_s)` even when nothing drifted. Reflex ignores an entity for 5 s after it fires (`core/reflex/attention.py`), and either the restore or the previous sample's last event may have touched the entity this golden changes first.
   - Note `time.monotonic()` and `time.time()` together.
   - Read the stored user timezone, so the sample can put it back.
2. **`clock`.**
   - In an hour's last two minutes, sleep `clock_wait_s`.
   - Set the zone `zone_for_hour(hour, now)`.
   - Record a `ClockSet` and add "it is now HH:MM" to the transcript.
3. **`ha_event`.** When the golden watches Reflex, wait up to `reflex_timeout_s` for a completed System 1 call that arrived after the push. The step's `settle` replaces that timeout. Otherwise the slice-1 waits apply unchanged.
4. **`advance_trigger`.**
   - Choose the newest trigger created in this sample that has a `run_at`, optionally narrowed by name.
   - If there is none, the transcript says "time passes, but no reminder was set" and the step ends. That is Alfred's failure: the checks score it.
   - Otherwise record an `Advance` and wait up to `fire_timeout_s` (or the step's `settle`) for its `TriggerFired`. Then settle `settle_s` for its notification.
5. **`dnd`.** Set DND through the bus, add "do not disturb on/off" to the transcript, and sleep `settle`.
6. **After the steps.**
   - Collect: triggers, fires, notifications, deferred, and Reflex calls (only when System 1 was called).
   - Clean up:
     - delete this sample's triggers;
     - `clear_dnd` if a `dnd` step ran;
     - put the user timezone back if it changed, whether a `clock` step or an actor's `tz` changed it.
   - A sample that raised skips cleanup. Its stack is dirty and is restarted before the next sample.

- [ ] **Step 1: Add `FakeBus` to `tests/evals/harness/factories.py`**

Add these imports:

```python
import time

from bus.schemas.events import TriggerCreated, TriggerFired
from core.notifications.schema import Notification, Urgency
from evals.harness.bus import Entry
```

And `from datetime import datetime` and `from core.reflex.tool_registry import ToolInfo` under `TYPE_CHECKING`. Then add:

```python
class FakeBus:
    """An in-memory ``Bus``. ``advance_trigger`` fires the trigger and sends its
    notification at once, as the engine does with a due ``run_at``."""

    def __init__(self, *, tz: str | None = None, tools: list[ToolInfo] | None = None) -> None:
        self.entries: list[Entry] = []  # alfred:events
        self.sent: list[Entry] = []  # the notification dispatch stream
        self.held: list[str] = []  # the deferred list
        self.tools = tools or []
        self.tz = tz
        self.tz_set: list[str | None] = []
        self.dnd: list[bool] = []
        self.cleared = 0
        self.advanced: list[str] = []
        self.deleted: list[str] = []
        self._names: dict[str, str] = {}

    def created(
        self,
        trigger_id: str = "t1",
        name: str = "Laundry reminder",
        conditions: dict[str, Any] | None = None,
    ) -> None:
        """System 2 created a time trigger, now."""
        event = TriggerCreated(
            trigger_id=trigger_id,
            trigger_type="time",
            name=name,
            created_by="tool-call",
            conditions=conditions or {"run_at": "2026-10-08T20:00:00+00:00"},
        )
        self._names[trigger_id] = name
        self.entries.append(Entry(wall=time.time(), data={"event": event.model_dump_json()}))

    def notify(self, title: str, urgency: str = "informational", wall: float | None = None) -> None:
        note = Notification(title=title, body="", urgency=Urgency(urgency), source="trigger-engine")
        stamp = time.time() if wall is None else wall
        self.sent.append(Entry(wall=stamp, data={"notification": note.model_dump_json()}))

    def hold(self, title: str) -> None:
        note = Notification(
            title=title, body="", urgency=Urgency.INFORMATIONAL, source="trigger-engine"
        )
        self.held.append(note.model_dump_json())

    async def events(self, since_wall: float) -> list[Entry]:
        return [e for e in self.entries if e.wall >= since_wall]

    async def notifications(self, since_wall: float) -> list[Entry]:
        return [e for e in self.sent if e.wall >= since_wall]

    async def deferred(self) -> list[str]:
        return list(self.held)

    async def reflex_tools(self) -> list[ToolInfo]:
        return list(self.tools)

    async def advance_trigger(self, trigger_id: str, now: datetime) -> bool:
        if trigger_id not in self._names:
            return False
        self.advanced.append(trigger_id)
        name = self._names.pop(trigger_id)  # a one-shot is deleted when it fires
        fired = TriggerFired(trigger_id=trigger_id, trigger_name=name, trigger_type="time")
        self.entries.append(Entry(wall=time.time(), data={"event": fired.model_dump_json()}))
        self.notify(f"Trigger: {name}")
        return True

    async def delete_triggers(self, trigger_ids: list[str]) -> None:
        self.deleted.extend(trigger_ids)

    async def user_timezone(self) -> str | None:
        return self.tz

    async def set_user_timezone(self, tz: str | None) -> None:
        self.tz = tz
        self.tz_set.append(tz)

    async def set_dnd(self, active: bool) -> None:
        self.dnd.append(active)

    async def clear_dnd(self) -> None:
        self.cleared += 1
```

- [ ] **Step 2: Write the failing tests** (`tests/evals/harness/test_driver.py`)

Update the imports:

```python
from datetime import UTC, datetime

from evals.harness.driver import HarnessError, PlayContext, build_request, clock_wait_s, play
from evals.harness.evidence import ClockSet, Evidence, HaCall, LlmCall
from tests.evals.harness.factories import FakeBus
```

Replace `ctx()` so that every test gets a bus and can override any field:

```python
def ctx(  # type: ignore[no-untyped-def]
    send, ha: FakeHA | None = None, proxy: LlmProxy | None = None, bus: FakeBus | None = None, **kw
) -> PlayContext:
    return PlayContext(
        send=send,
        fake_ha=ha or FakeHA(load_world("apartment")),
        proxy=proxy or LlmProxy("http://x"),
        bus=bus or FakeBus(),
        settle_s=0,
        restore_settle_s=0,
        poll_s=0.01,
        **kw,
    )
```

Then append:

```python
class Acting(Recorder):
    """A send that also does *act*, as Alfred would while answering."""

    def __init__(self, act: Callable[[], None]) -> None:
        super().__init__()
        self.act = act

    async def __call__(self, request: UserRequest, timeout: float) -> AlfredResponse:
        self.act()
        return await super().__call__(request, timeout)


async def played(play_ctx: PlayContext, **fields: object) -> Evidence:
    [variant] = expand_variants(scenario(**fields))
    return await play(play_ctx, variant, epoch=1)


async def test_a_clock_step_sets_the_zone_for_its_hour_and_the_sample_puts_it_back() -> None:
    bus = FakeBus(tz="America/Denver")
    now = datetime(2026, 10, 8, 15, 10, tzinfo=UTC)
    ev = await played(
        ctx(Recorder(), bus=bus, now=lambda: now),
        steps=[{"clock": {"hour": 22}}, {"user": "Hello."}],
    )
    assert ev.clocks == [ClockSet(step=0, hour=22, tz="Etc/GMT-7")]
    assert ev.step_kinds == ["clock", "user"]
    assert ev.transcript[0].text == "it is now 22:10"
    assert bus.tz_set == ["Etc/GMT-7", "America/Denver"]


@pytest.mark.parametrize(
    ("minute", "second", "wait"),
    [(10, 0, 0.0), (57, 59, 0.0), (58, 0, 121.0), (59, 30, 31.0), (59, 59, 2.0)],
)
def test_clock_waits_out_the_last_minutes_of_an_hour(minute: int, second: int, wait: float) -> None:
    assert clock_wait_s(datetime(2026, 10, 8, 15, minute, second, tzinfo=UTC)) == wait


async def test_advance_brings_the_samples_trigger_forward_and_waits_for_its_fire() -> None:
    bus = FakeBus()
    ev = await played(
        ctx(Acting(lambda: bus.created("t1", "Laundry reminder")), bus=bus),
        steps=[{"user": "Remind me in 20 minutes to move the laundry."}, {"advance_trigger": None}],
    )
    assert bus.advanced == ["t1"]
    [advance] = ev.advances
    assert (advance.step, advance.name) == (1, "Laundry reminder")
    assert [f.trigger_id for f in ev.triggers_fired] == ["t1"]
    assert [n.title for n in ev.notifications] == ["Trigger: Laundry reminder"]
    assert ev.step_kinds == ["user", "advance_trigger"]
    assert bus.deleted == ["t1"]  # cleanup; a fired one-shot is already gone, which is fine


async def test_advance_without_a_trigger_is_alfreds_failure_not_the_harness() -> None:
    bus = FakeBus()
    ev = await played(
        ctx(Recorder(), bus=bus),
        steps=[{"user": "Remind me later."}, {"advance_trigger": None}],
    )
    assert ev.advances == [] and bus.advanced == []
    assert ev.transcript[-1].text == "time passes, but no reminder was set"


async def test_play_cleans_up_triggers_dnd_and_clock() -> None:
    bus = FakeBus()
    ev = await played(
        ctx(Acting(lambda: bus.created("t9")), bus=bus, now=lambda: datetime(2026, 10, 8, 3, 0, tzinfo=UTC)),
        steps=[{"clock": {"hour": 22}}, {"dnd": True, "settle": 0}, {"user": "Remind me."}],
    )
    assert "do not disturb on" in [t.text for t in ev.transcript]
    assert bus.dnd == [True] and bus.cleared == 1
    assert bus.deleted == ["t9"]
    assert bus.tz_set[-1] is None  # nothing was stored before the sample


async def test_reflex_golden_waits_out_the_attention_cooldown_after_a_restore() -> None:
    ha = FakeHA(load_world("apartment"))
    # The bedroom lamp starts off and the golden sets it off, so the golden never drifts it.
    event = {"ha_event": {"entity_id": "light.bedroom_lamp", "state": "off"}, "settle": 0}
    watches = scenario(steps=[event], expect=[{"reflex_decision": {"decision": "none"}}])
    await ha.set_state("light.bedroom_lamp", "on")  # drifted: the restore pushes it back
    elapsed, _ = await timed_play(ctx(Recorder(), ha, reflex_cooldown_s=0.4), watches)
    assert elapsed >= 0.4
    # Nothing to restore now, and the wait still applies: the last sample's event started
    # a cooldown on the very entity this golden changes.
    elapsed, _ = await timed_play(ctx(Recorder(), ha, reflex_cooldown_s=0.4), watches)
    assert elapsed >= 0.4
    plain = scenario(steps=[event])
    elapsed, _ = await timed_play(ctx(Recorder(), ha, reflex_cooldown_s=0.4), plain)
    assert elapsed < 0.3  # a golden that does not watch Reflex does not wait


async def test_an_ha_event_waits_for_system1_when_a_reflex_check_watches() -> None:
    proxy = LlmProxy("http://x")

    async def system1_answers() -> None:
        await asyncio.sleep(0.2)
        proxy.calls.append(
            LlmCall(
                t=time.monotonic(),
                role="system1",
                latency_ms=5.0,
                status=200,
                response_text='{"decision": "none", "reason": "quiet"}',
            )
        )

    answering = asyncio.create_task(system1_answers())
    elapsed, ev = await timed_play(
        ctx(Recorder(), proxy=proxy, reflex_cooldown_s=0, reflex_timeout_s=5),
        scenario(steps=[LAMP_ON], expect=[{"reflex_decision": {"decision": "none"}}]),
    )
    await answering
    assert 0.2 <= elapsed < 2
    assert [(c.decision, c.reason) for c in ev.reflex] == [("none", "quiet")]


async def test_an_ha_event_waits_out_the_reflex_timeout_when_system1_stays_quiet() -> None:
    elapsed, ev = await timed_play(
        ctx(Recorder(), reflex_cooldown_s=0, reflex_timeout_s=0.3),
        scenario(steps=[LAMP_ON], expect=[{"reflex_decision": {"decision": "none"}}]),
    )
    assert elapsed >= 0.3 and ev.reflex == []


async def test_sent_and_held_notifications_from_the_sample_are_evidence() -> None:
    bus = FakeBus()
    bus.notify("Trigger: Before the sample", wall=time.time() - 60)

    def alfred_notifies() -> None:
        bus.notify("Trigger: Vet", urgency="urgent")
        bus.hold("Trigger: Plants")

    ev = await played(ctx(Acting(alfred_notifies), bus=bus), steps=[{"user": "Hello."}])
    assert [(n.title, n.urgency) for n in ev.notifications] == [("Trigger: Vet", "urgent")]
    assert ev.started_at <= (ev.notifications[0].t or 0) <= ev.ended_at
    assert [n.title for n in ev.deferred] == ["Trigger: Plants"]
```

In `tests/evals/harness/test_tasks.py`, import `FakeBus` from `tests.evals.harness.factories` and pass `bus=FakeBus()` in `context()`'s `PlayContext(...)`.

- [ ] **Step 3: Run the tests and watch them fail**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_driver.py -q`
Expected: FAIL. It cannot import `clock_wait_s`, and `PlayContext` has no `bus`.

- [ ] **Step 4: Implement in `evals/harness/driver.py`**

Imports:

```python
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from evals.harness.bus import zone_for_hour
from evals.harness.collect import deferred_records, notification_records, trigger_records
from evals.harness.evidence import Advance, ClockSet, Evidence, Reply, TranscriptTurn
from evals.harness.reflex import reflex_calls
from evals.harness.scenario import (
    Actor,
    AdvanceTriggerStep,
    ClockStep,
    DndStep,
    HaEventStep,
    ScenarioVariant,
    UserStep,
    WaitStep,
    step_kind,
)
```

Under `TYPE_CHECKING`, also import `from evals.harness.bus import Bus`.

Before `PlayContext`, add:

```python
def _utc_now() -> datetime:
    return datetime.now(UTC)


def clock_wait_s(now: datetime) -> float:
    """How long to wait before a clock step, so the hour it sets is still the hour when
    Reflex reads it. Zero, except in an hour's last two minutes, when the wait runs one
    second into the next hour."""
    if now.minute < 58:
        return 0.0
    return float((60 - now.minute) * 60 - now.second + 1)
```

`PlayContext` becomes:

```python
@dataclass
class PlayContext:
    send: SendFn
    fake_ha: FakeHA
    proxy: LlmProxy
    bus: Bus
    reply_timeout_s: float = 120.0
    settle_s: float = 2.0
    restore_settle_s: float = 2.0
    # After an ha_event: how long to wait for the call_service a golden expects, and the
    # quiet window when it expects none. A step's ``settle`` replaces either.
    ha_call_timeout_s: float = 30.0
    ha_event_window_s: float = 5.0
    # After the last step: how long an LLM call still upstream may take to be recorded.
    llm_idle_timeout_s: float = 120.0
    signal_number: str = EVAL_SIGNAL_NUMBER
    # A golden that watches Reflex: how long to wait for System 1 after an ha_event (a
    # step's ``settle`` replaces it), and how long to let Reflex's 5 s attention cooldown
    # run out before the first step.
    reflex_timeout_s: float = 15.0
    reflex_cooldown_s: float = 6.0
    # How long a trigger brought forward may take to fire; how often the bus is polled.
    fire_timeout_s: float = 15.0
    poll_s: float = 0.2
    now: Callable[[], datetime] = _utc_now
```

After `session_id_for`, add:

```python
async def _wait_until(check: Callable[[], Awaitable[bool]], timeout_s: float, poll_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while True:
        if await check():
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(poll_s)


async def _set_clock(ctx: PlayContext, ev: Evidence, index: int, hour: int) -> None:
    if (wait := clock_wait_s(ctx.now())) > 0:
        await asyncio.sleep(wait)
    now = ctx.now()
    zone = zone_for_hour(hour, now)
    await ctx.bus.set_user_timezone(zone)
    ev.clocks.append(ClockSet(step=index, hour=hour, tz=zone))
    local = now.astimezone(ZoneInfo(zone))
    ev.transcript.append(TranscriptTurn(role="event", text=f"it is now {local:%H:%M}"))


async def _advance(
    ctx: PlayContext, ev: Evidence, index: int, step: AdvanceTriggerStep, started_wall: float
) -> None:
    """Make the sample's newest one-time trigger due now, then wait for it to fire."""
    created, _ = trigger_records(await ctx.bus.events(started_wall), ev.started_at, started_wall)
    name = step.advance_trigger.name
    candidates = [
        r
        for r in created
        if r.conditions.get("run_at") is not None
        and (name is None or name.lower() in r.name.lower())
    ]
    if not candidates:
        ev.transcript.append(
            TranscriptTurn(role="event", text="time passes, but no reminder was set")
        )
        return
    trigger = candidates[-1]
    t = time.monotonic()
    if not await ctx.bus.advance_trigger(trigger.trigger_id, ctx.now()):
        ev.transcript.append(
            TranscriptTurn(role="event", text=f"time passes, but {trigger.name!r} is gone")
        )
        return
    ev.advances.append(Advance(step=index, trigger_id=trigger.trigger_id, name=trigger.name, t=t))
    ev.transcript.append(TranscriptTurn(role="event", text=f"time passes: {trigger.name!r} is due"))

    async def fired() -> bool:
        _, fires = trigger_records(await ctx.bus.events(started_wall), ev.started_at, started_wall)
        return any(f.trigger_id == trigger.trigger_id for f in fires)

    timeout = ctx.fire_timeout_s if step.settle is None else step.settle
    if await _wait_until(fired, timeout, ctx.poll_s):
        await asyncio.sleep(ctx.settle_s)  # for the notification it sends


async def _collect(ctx: PlayContext, ev: Evidence, started_wall: float) -> list[str]:
    """Fill in the bus's evidence. Returns the ids of every trigger the sample created."""
    created, fired = trigger_records(
        await ctx.bus.events(started_wall), ev.started_at, started_wall
    )
    ev.triggers_created, ev.triggers_fired = created, fired
    ev.notifications = notification_records(
        await ctx.bus.notifications(started_wall), ev.started_at, started_wall
    )
    ev.deferred = deferred_records(await ctx.bus.deferred())
    if any(c.role == "system1" for c in ev.llm_calls):
        tools = await ctx.bus.reflex_tools()
        ev.reflex = reflex_calls(ev.llm_calls, tools, ctx.fake_ha.world)
    return [r.trigger_id for r in created]


async def _clean_up(
    ctx: PlayContext, created: list[str], touched_dnd: bool, tz_before: str | None
) -> None:
    """Leave the container as the sample found it, for the next sample in it."""
    if created:
        await ctx.bus.delete_triggers(created)
    if touched_dnd:
        await ctx.bus.clear_dnd()
    if await ctx.bus.user_timezone() != tz_before:  # a clock step, or an actor's tz
        await ctx.bus.set_user_timezone(tz_before)
```

In `play()`:

- Replace the restore with:

  ```python
      restored = await ctx.fake_ha.restore_world()
      if scenario.watches_reflex:
          await asyncio.sleep(max(ctx.restore_settle_s, ctx.reflex_cooldown_s))
      elif restored:
          await asyncio.sleep(ctx.restore_settle_s)
  ```

- Right after `started = time.monotonic()`, add `started_wall = time.time()`. After `ev` is built, add `tz_before = await ctx.bus.user_timezone()` and `touched_dnd = False`.
- At the top of the loop, after `ev.step_started.append(...)`, add:

  ```python
          kind = step_kind(step)
          assert kind is not None  # a parsed step always has one
          ev.step_kinds.append(kind)
  ```

- In the `HaEventStep` case, put this branch before the `outstanding_calls` one, so the existing `if`/`else` becomes its `elif`/`else`:

  ```python
                  if scenario.watches_reflex:

                      async def system1_answered(since: float = pushed) -> bool:
                          calls = ctx.proxy.calls_between(since, time.monotonic())
                          return any(c.role == "system1" for c in calls)

                      timeout = ctx.reflex_timeout_s if step.settle is None else step.settle
                      await _wait_until(system1_answered, timeout, ctx.poll_s)
                  elif outstanding := outstanding_calls(scenario, index, ev, ctx.fake_ha):
  ```

- Add these cases before `case _:`:

  ```python
              case ClockStep():
                  await _set_clock(ctx, ev, index, step.clock.hour)
              case AdvanceTriggerStep():
                  await _advance(ctx, ev, index, step, started_wall)
              case DndStep():
                  await ctx.bus.set_dnd(step.dnd)
                  touched_dnd = True
                  state = "on" if step.dnd else "off"
                  ev.transcript.append(TranscriptTurn(role="event", text=f"do not disturb {state}"))
                  await asyncio.sleep(step.settle)
  ```

- Replace the tail, from `ev.ha_states = …` through `return ev`, with:

  ```python
      ev.ha_states = ctx.fake_ha.states()
      # Cleanup runs only on this path: a sample that raised leaves a dirty stack, and the
      # next sample restarts it (tasks.reset_or_recover).
      created = await _collect(ctx, ev, started_wall)
      await _clean_up(ctx, created, touched_dnd, tz_before)
      return ev
  ```

In `evals/harness/orchestrate.py`'s `make_ctx`, pass `bus=stack.bus` to `PlayContext`.

- [ ] **Step 5: Run the tests and watch them pass**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_driver.py tests/evals/harness/test_tasks.py tests/evals/harness/test_orchestrate.py -q`
Expected: PASS. If `test_orchestrate.py` does not exist, drop it from the command. The slice-1 driver tests still pass, because a golden that does not watch Reflex has the same waits as before.

- [ ] **Step 6: Type-check and commit**

```bash
.venv/bin/ruff check --fix evals/harness tests/evals/harness
.venv/bin/ruff format evals/harness tests/evals/harness
.venv/bin/mypy evals
git add evals/harness/driver.py evals/harness/orchestrate.py tests/evals/harness
git commit -m "feat(evals): play clock, advance_trigger and dnd steps; wait for Reflex; clean up"
```

Expected: mypy reports no errors. The `assert_never` in `play()` now covers every step kind.

---

### Task 10: A bus failure is the harness's failure

**Files:**
- Modify: `evals/harness/tasks.py`
- Test: `tests/evals/harness/test_tasks.py`

**Interfaces:**
- Consumes: `BusError` (Task 7), `FakeBus` (Task 9).
- Produces: `play_scenario` treats `BusError` and `redis.exceptions.RedisError` the way it treats `HarnessError` and `StackError`. The sample scores `E`, and the stack is marked dirty, so the next sample restarts it.

- [ ] **Step 1: Write the failing test** (append to `tests/evals/harness/test_tasks.py`)

Give `context()` a `bus: FakeBus | None = None` parameter, before `**scenario_fields`, and pass `bus=bus or FakeBus()` to `PlayContext`. Then add:

```python
from redis.exceptions import RedisError

from evals.harness.bus import BusError


class BrokenBus(FakeBus):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    async def user_timezone(self) -> str | None:
        raise self.error


@pytest.mark.parametrize(
    "error",
    [RedisError("Connection closed by server."), BusError("POST /api/admin/dnd answered 401")],
    ids=["redis", "bus"],
)
async def test_a_bus_failure_errors_the_sample_and_dirties_the_stack(
    tmp_path: Path, error: Exception
) -> None:
    stack = FakeStack([True])
    ctx = context(stack, bus=BrokenBus(error))
    ctx.restarts_left = 0
    runs = await evaluate(ctx, tmp_path)
    assert [r.value for r in runs] == ["E", "E"] and stack.restarts == 0
    first = next(r for r in runs if not r.sample_id.endswith("~1"))
    assert str(error) in (first.error or "")
    # Only a dirty stack makes the next sample ask for the restart it cannot have.
    later = next(r for r in runs if r.sample_id.endswith("~1"))
    assert "needs a restart" in (later.error or "")
```

- [ ] **Step 2: Run the test and watch it fail**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_tasks.py -k bus_failure -q`
Expected: FAIL. The later sample's error repeats the bus error instead of saying it "needs a restart": the stack was never marked dirty.

- [ ] **Step 3: Implement** (`evals/harness/tasks.py`)

Import `from redis.exceptions import RedisError` and `from evals.harness.bus import BusError`, then widen the catch in `play_scenario`:

```python
        except (HarnessError, StackError, BusError, RedisError) as exc:
            ctx.dirty = _first_line(exc)
            raise
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `.venv/bin/python -m pytest tests/evals/harness -q`
Expected: PASS: the whole harness suite.

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff check --fix evals/harness/tasks.py tests/evals/harness/test_tasks.py
.venv/bin/ruff format evals/harness/tasks.py tests/evals/harness/test_tasks.py
git add evals/harness/tasks.py tests/evals/harness/test_tasks.py
git commit -m "fix(evals): a bus or redis failure errors the sample and dirties the stack"
```

---
### Task 11: The `reflex` suite

The goldens are the reflex spec's table ("Goldens for the PRD suite's `reflex` suite"), plus three negative cases and one `pending` golden for tiered autonomy.

**Authoring rules for this suite:**
- Each golden's checks read its last `ha_event` unless `at_step` says otherwise. The earlier events are setup.
- **Setup events.**
  - Reflex attends to lights, switches, media players, scenes, climate, locks and people, and to door/motion/occupancy/presence/window/garage_door sensors (`core/reflex/attention_seed.yaml`). For those, the driver waits for System 1's answer, so that call stays in its own step's window.
  - A setup event Reflex does not attend to (the sun, a temperature sensor) gets `settle: 1`, so the driver does not wait 15 s for a call that never comes.
- **One entity changed twice:** put `wait: 6` between the two changes. Reflex ignores an entity for 5 s after it fires.
- **Quote states YAML would read as booleans:** `"on"` and `"off"`.
- **The world at the start of every sample:**
  - the living-room lamp and the kitchen pendants on;
  - the ceiling light, the bedroom lamp and the TV off;
  - Alex and Sam home;
  - the sun up.

**Files:**
- Create: `evals/suites/reflex/*.yaml` (13 files, listed below)
- Modify:
  - `evals/coverage.yaml`
  - `tests/evals/test_goldens_load.py`

- [ ] **Step 1: Write the failing guard** (`tests/evals/test_goldens_load.py`)

Make these changes:
- set `SUITES = ["conversation", "home_control", "reflex"]`;
- add `and len(suites["reflex"]) >= 13` to the count assert in `test_every_golden_loads_and_names_real_entities`;
- append:

```python
def test_reflex_targets_name_a_real_entity_or_room() -> None:
    world = load_world("apartment")
    names = {e.entity_id for e in world.entities} | {a.area_id for a in world.areas}
    for s in _goldens().values():
        for check in s.expect:
            target = getattr(check.params, "target", None)
            assert target is None or target in names, f"{s.path}: {target}"
```

Run: `.venv/bin/python -m pytest tests/evals/test_goldens_load.py -q`
Expected: FAIL with `unknown suite(s) ['reflex']`.

- [ ] **Step 2: Write the goldens**

`evals/suites/reflex/judgment_tv_night_dims.yaml`:

```yaml
id: reflex.judgment.tv_night_dims
prd: [4.4.reflex-judgment, 4.4.lights-scenes]
status: shipped
tags: [judgment, media]
steps:
  - clock: {hour: 22}
  - ha_event: {entity_id: media_player.living_room_tv, state: paused}
  - wait: 6
  - ha_event: {entity_id: media_player.living_room_tv, state: playing}
expect:
  - reflex_decision: {decision: [act, ask], target: living_room}
```

`evals/suites/reflex/judgment_tv_noon_noop.yaml`:

```yaml
id: reflex.judgment.tv_noon_noop
prd: [4.4.reflex-judgment]
status: shipped
tags: [judgment, media]
steps:
  - clock: {hour: 12}
  - ha_event: {entity_id: media_player.living_room_tv, state: paused}
  - wait: 6
  - ha_event: {entity_id: media_player.living_room_tv, state: playing}
expect:
  - reflex_decision: {decision: none}
```

`evals/suites/reflex/judgment_last_out_lights_on.yaml`:

```yaml
id: reflex.judgment.last_out_lights_on
prd: [4.4.reflex-judgment]
status: shipped
tags: [judgment, presence]
steps:
  - clock: {hour: 19}
  - ha_event: {entity_id: person.sam, state: not_home}
  - ha_event: {entity_id: person.alex, state: not_home}
expect:
  - reflex_decision: {decision: [act, ask], tool: home.light_turn_off}
```

`evals/suites/reflex/judgment_one_leaves_other_home.yaml`:

```yaml
id: reflex.judgment.one_leaves_other_home
prd: [4.4.reflex-judgment]
status: shipped
tags: [judgment, presence]
steps:
  - clock: {hour: 19}
  - ha_event: {entity_id: person.sam, state: not_home}
expect:
  - reflex_decision: {decision: none}
```

`evals/suites/reflex/judgment_arrive_after_dark.yaml`:

```yaml
id: reflex.judgment.arrive_after_dark
prd: [4.4.reflex-judgment]
status: shipped
tags: [judgment, presence]
steps:
  - clock: {hour: 20}
  - ha_event: {entity_id: sun.sun, state: below_horizon}
    settle: 1  # Reflex does not attend to the sun
  - ha_event: {entity_id: light.living_room_lamp, state: "off"}
  - ha_event: {entity_id: light.kitchen_pendants, state: "off"}
  - ha_event: {entity_id: person.sam, state: not_home}
  - ha_event: {entity_id: person.alex, state: not_home}
  - wait: 6  # Reflex ignores Alex for 5 s after the last change
  - ha_event: {entity_id: person.alex, state: home}
expect:
  - reflex_decision: {decision: [act, ask], tool: home.light_turn_on}
```

`evals/suites/reflex/judgment_arrive_daytime_noop.yaml`:

```yaml
id: reflex.judgment.arrive_daytime_noop
prd: [4.4.reflex-judgment]
status: shipped
tags: [judgment, presence]
steps:
  - clock: {hour: 13}
  - ha_event: {entity_id: light.living_room_lamp, state: "off"}
  - ha_event: {entity_id: light.kitchen_pendants, state: "off"}
  - ha_event: {entity_id: person.sam, state: not_home}
  - ha_event: {entity_id: person.alex, state: not_home}
  - wait: 6
  - ha_event: {entity_id: person.alex, state: home}
expect:
  - reflex_decision: {decision: none}
```

`evals/suites/reflex/judgment_bedtime_lights_on.yaml`:

```yaml
id: reflex.judgment.bedtime_lights_on
prd: [4.4.reflex-judgment]
status: shipped
tags: [judgment, lights]
steps:
  - clock: {hour: 0}
  - ha_event: {entity_id: light.bedroom_lamp, state: "on"}
  - wait: 6
  - ha_event: {entity_id: light.bedroom_lamp, state: "off"}
expect:
  # Off to bed with the living room still lit: worth a question, not a decision.
  - reflex_decision: {decision: ask}
```

`evals/suites/reflex/judgment_media_pause_noop.yaml`:

```yaml
id: reflex.judgment.media_pause_noop
prd: [4.4.reflex-judgment]
status: shipped
tags: [judgment, media]
steps:
  - clock: {hour: 19}
  - ha_event: {entity_id: media_player.living_room_tv, state: playing}
  - wait: 6
  - ha_event: {entity_id: media_player.living_room_tv, state: paused}
  - wait: 6
  - ha_event: {entity_id: media_player.living_room_tv, state: playing}
expect:
  - reflex_decision: {decision: none, at_step: 3}  # the pause
  - reflex_decision: {decision: none}  # the resume
```

`evals/suites/reflex/judgment_lamp_on_not_repeated.yaml`:

```yaml
id: reflex.judgment.lamp_on_not_repeated
prd: [4.4.reflex-judgment]
status: shipped
tags: [judgment, lights]
steps:
  - clock: {hour: 20}
  - ha_event: {entity_id: light.living_room_ceiling, state: "on"}
expect:
  # Someone just turned it on: proposing to turn it on is noise.
  - reflex_not_proposed: {tool: home.light_turn_on, target: light.living_room_ceiling}
```

`evals/suites/reflex/judgment_sensor_noise_noop.yaml`:

```yaml
id: reflex.judgment.sensor_noise_noop
prd: [4.4.reflex-judgment]
status: shipped
tags: [judgment, noise]
steps:
  - ha_event: {entity_id: sensor.living_room_temperature, state: "21.6"}
    settle: 5
expect:
  - reflex_decision: {decision: none}
```

`evals/suites/reflex/autonomy_door_unlocked_night.yaml`:

```yaml
id: reflex.autonomy.door_unlocked_night
prd: [4.4.tiered-autonomy]
status: pending  # the PRD row is Planned; #286 decides the tiers
tags: [autonomy]
steps:
  - clock: {hour: 2}
  - ha_event: {entity_id: person.sam, state: not_home}
  - ha_event: {entity_id: person.alex, state: not_home}
  - ha_event: {entity_id: lock.front_door, state: unlocked}
expect:
  # Never act on its own here. Until the tiers land, "none" is allowed too.
  - reflex_decision: {decision: [ask, none]}
```

`evals/suites/reflex/latency_lamp_event.yaml`:

```yaml
id: reflex.latency.lamp_event
prd: [7.reflex-latency]
status: shipped
tags: [latency]
steps:
  - ha_event: {entity_id: light.living_room_ceiling, state: "on"}
expect:
  - latency: {metric: reflex_ms, max: 500}
```

`evals/suites/reflex/prompt_compact.yaml`:

```yaml
id: reflex.prompt.compact
prd: [4.4.reflex-judgment]
status: shipped
tags: [prompt]
steps:
  - ha_event: {entity_id: light.bedroom_lamp, state: "on"}
expect:
  - prompt_not_contains:
      role: system1
      any: ["button.", "notify.", "entity_picture", "supported_features"]
```

- [ ] **Step 3: Move the reflex rows in `evals/coverage.yaml`**

Replace these rows. Every other field stays as it is.

```yaml
  - {id: 4.3.s2-observes-s1, section: "4.3", prd: "System 2 observation of System 1", pending_suites: [memory]}
  - {id: 4.4.lights-scenes, section: "4.4", prd: "Lights and scenes control", suites: [home_control, reflex]}
  - {id: 4.4.reflex-judgment, section: "4.4", prd: "Reflex judgment calls", suites: [reflex]}
  - {id: 4.4.tiered-autonomy, section: "4.4", prd: "Tiered autonomy", suites: [reflex]}
  - {id: 7.reflex-latency, section: "7", prd: "Reflex latency", suites: [reflex]}
```

- [ ] **Step 4: Run the loader, coverage and CLI tests**

Run: `.venv/bin/python -m pytest tests/evals/test_goldens_load.py tests/evals/test_prd_coverage.py tests/evals/test_cli.py -q`
Expected: PASS. If a golden fails to load, the message names the file and the field. Fix the golden, not the loader.

Then run `.venv/bin/alfred evals list reflex`.
Expected: 13 goldens, one of them `pending`.

- [ ] **Step 5: Commit**

```bash
git add evals/suites/reflex evals/coverage.yaml tests/evals/test_goldens_load.py
git commit -m "feat(evals): the reflex suite: judgment, latency and prompt goldens (#298)"
```

---

### Task 12: The `triggers` suite

**Files:**
- Create: `evals/suites/triggers/*.yaml` (9 files)
- Modify:
  - `evals/coverage.yaml`
  - `tests/evals/test_goldens_load.py`

- [ ] **Step 1: Write the failing guard**

In `tests/evals/test_goldens_load.py`:
- append `"triggers"` to `SUITES`;
- add `and len(suites["triggers"]) >= 9` to the count assert;
- append:

```python
def test_trigger_conditions_name_real_entities() -> None:
    world_ids = {e.entity_id for e in load_world("apartment").entities}
    for s in _goldens().values():
        for check in s.expect:
            conditions = getattr(check.params, "conditions", None) or {}
            entity = conditions.get("entity_id")
            assert not isinstance(entity, str) or entity in world_ids, f"{s.path}: {entity}"
```

Run: `.venv/bin/python -m pytest tests/evals/test_goldens_load.py -q`
Expected: FAIL with `unknown suite(s) ['triggers']`.

- [ ] **Step 2: Write the goldens**

`evals/suites/triggers/relative_laundry_twenty_minutes.yaml`:

```yaml
id: triggers.relative.laundry_twenty_minutes
prd: [4.2.relative-reminders, 4.2.dynamic-triggers]
status: shipped
tags: [relative]
steps:
  - user: "Remind me in 20 minutes to move the laundry to the dryer."
    variants:
      - "In twenty minutes, remind me to move the laundry over."
      - "Set a reminder for 20 minutes from now: laundry into the dryer."
expect:
  - trigger_created: {type: time, run_in_seconds: {approx: 1200, tol: 60}}
  # The tool asks for run_in_seconds over clock arithmetic in run_at.
  - llm_tool_args:
      tool: triggers.create_trigger
      args: {conditions: {run_in_seconds: {approx: 1200, tol: 60}}}
  - judge:
      category: faithfulness
      rubric: "Does the reply confirm a reminder in about twenty minutes to move the laundry?"
```

`evals/suites/triggers/relative_hour_and_a_half.yaml`:

```yaml
id: triggers.relative.hour_and_a_half
prd: [4.2.relative-reminders]
status: shipped
tags: [relative]
steps:
  - user: "Remind me in an hour and a half to check on the bread."
expect:
  - trigger_created: {type: time, run_in_seconds: {approx: 5400, tol: 60}}
  - llm_tool_args:
      tool: triggers.create_trigger
      args: {conditions: {run_in_seconds: {approx: 5400, tol: 60}}}
```

`evals/suites/triggers/relative_negative_question.yaml`:

```yaml
id: triggers.relative.negative_question
prd: [4.2.relative-reminders]
status: shipped
tags: [relative, negative]
steps:
  - user: "How long should I steep green tea?"
expect:
  - trigger_not_created: {}
```

`evals/suites/triggers/fast_reminder_fires_quickly.yaml`:

```yaml
id: triggers.fast.reminder_fires_quickly
prd: [4.2.fast-reminders, 7.reminder-latency]
status: shipped
tags: [fast]
steps:
  - user: "Remind me in 30 minutes to stretch."
  - advance_trigger: {}
expect:
  - trigger_fired: {after_step: 1, within_s: 5}
  - latency: {metric: reminder_fire_ms, max: 5000}
  - notification: {source: trigger-engine, after_step: 1}
```

`evals/suites/triggers/timezone_new_york_seven_pm.yaml`:

```yaml
id: triggers.timezone.new_york_seven_pm
prd: [4.2.client-timezone, 4.2.dynamic-triggers]
status: shipped
tags: [timezone]
as: {tz: America/New_York}
steps:
  - user: "Remind me at 7pm to call my sister."
expect:
  - trigger_created: {type: time, at_local: {time: "19:00", tz: America/New_York}}
```

`evals/suites/triggers/timezone_tokyo_morning.yaml`:

```yaml
id: triggers.timezone.tokyo_morning
prd: [4.2.client-timezone]
status: shipped
tags: [timezone]
as: {tz: Asia/Tokyo}
steps:
  - user: "Remind me tomorrow at 8:15 in the morning to buy train tickets."
expect:
  - trigger_created: {type: time, at_local: {time: "08:15", tz: Asia/Tokyo}}
```

`evals/suites/triggers/recurring_weekday_vitamins.yaml`:

```yaml
id: triggers.recurring.weekday_vitamins
prd: [4.2.dynamic-triggers, principle.5, 5.not-rules-engine]
status: shipped
tags: [recurring]
steps:
  - user: "Remind me to take my vitamins every weekday at 7am."
expect:
  - trigger_created:
      type: time
      conditions: {cron: {regex: '0 7 \* \* (1-5|mon-fri)'}}
```

`evals/suites/triggers/sensor_front_door_opens.yaml`:

```yaml
id: triggers.sensor.front_door_opens
prd: [4.2.sensor-triggers, 4.2.dynamic-triggers, 5.not-rules-engine]
status: shipped
tags: [sensor]
steps:
  - user: "Let me know when the front door opens."
  - ha_event: {entity_id: binary_sensor.front_door, state: "on"}
expect:
  - trigger_created: {type: sensor, conditions: {entity_id: binary_sensor.front_door}}
  # Whatever state_match Alfred wrote, HA's "on" is what a door opening sends.
  - trigger_fired: {after_step: 1, within_s: 5}
```

`evals/suites/triggers/sensor_negative_status_question.yaml`:

```yaml
id: triggers.sensor.negative_status_question
prd: [4.2.sensor-triggers]
status: shipped
tags: [sensor, negative]
steps:
  - user: "Is the front door open?"
expect:
  - trigger_not_created: {}
  - judge:
      category: answered
      rubric: "Does the reply say whether the front door is open?"
```

- [ ] **Step 3: Move the trigger rows in `evals/coverage.yaml`**

```yaml
  - {id: principle.5, section: "3", prd: "Learns routines; doesn't demand programming.", suites: [triggers], pending_suites: [memory]}
  - {id: 4.2.dynamic-triggers, section: "4.2", prd: "Dynamic triggers created by conversation", suites: [triggers]}
  - {id: 4.2.sensor-triggers, section: "4.2", prd: "Sensor-driven triggers on live home state", suites: [triggers]}
  - {id: 4.2.fast-reminders, section: "4.2", prd: "Sub-5-second reminder firing", suites: [triggers]}
  - {id: 4.2.client-timezone, section: "4.2", prd: "Client-timezone awareness", suites: [triggers]}
  - {id: 4.2.relative-reminders, section: "4.2", prd: "Relative reminders", suites: [triggers]}
  - {id: 7.reminder-latency, section: "7", prd: "Reminder latency", suites: [triggers]}
  - {id: 5.not-rules-engine, heading: "5. What Alfred is not", suites: [triggers], pending_suites: [memory]}
```

- [ ] **Step 4: Run the loader, coverage and CLI tests**

Run: `.venv/bin/python -m pytest tests/evals/test_goldens_load.py tests/evals/test_prd_coverage.py tests/evals/test_cli.py -q`
Expected: PASS. Then `.venv/bin/alfred evals list triggers` lists 9 goldens.

- [ ] **Step 5: Commit**

```bash
git add evals/suites/triggers evals/coverage.yaml tests/evals/test_goldens_load.py
git commit -m "feat(evals): the triggers suite: relative, timezone, recurring, sensor and fast reminders"
```

---

### Task 13: The `notifications` suite

**Files:**
- Create: `evals/suites/notifications/*.yaml` (5 files)
- Modify:
  - `evals/coverage.yaml`
  - `tests/evals/test_goldens_load.py`

- [ ] **Step 1: Write the failing guard**

In `tests/evals/test_goldens_load.py`, append `"notifications"` to `SUITES`, and add `and len(suites["notifications"]) >= 5` to the count assert.

Run: `.venv/bin/python -m pytest tests/evals/test_goldens_load.py -q`
Expected: FAIL with `unknown suite(s) ['notifications']`.

- [ ] **Step 2: Write the goldens**

`evals/suites/notifications/reminder_default_urgency.yaml`:

```yaml
id: notifications.reminder.default_urgency
prd: [4.2.proactive-notifications, 4.2.notification-delivery]
status: shipped
tags: [reminder]
steps:
  - user: "Remind me in 10 minutes to check the oven."
  - advance_trigger: {}
expect:
  - notification: {source: trigger-engine, urgency: informational, after_step: 1}
```

`evals/suites/notifications/reminder_urgent.yaml`:

```yaml
id: notifications.reminder.urgent
prd: [4.2.proactive-notifications]
status: shipped
tags: [reminder, urgency]
steps:
  - user: "Remind me in 15 minutes to leave for the airport. It's urgent, I can't miss it."
  - advance_trigger: {}
expect:
  - trigger_created: {type: time, urgency: urgent}
  - notification: {source: trigger-engine, urgency: urgent, after_step: 1}
```

`evals/suites/notifications/dnd_defers_informational.yaml`:

```yaml
id: notifications.dnd.defers_informational
prd: [principle.1, 4.2.proactive-notifications]
status: shipped
tags: [dnd]
steps:
  - dnd: on
  - user: "Remind me in 10 minutes to water the plants."
  - advance_trigger: {}
expect:
  - notification: {source: trigger-engine, deferred: true}
```

`evals/suites/notifications/dnd_urgent_breaks_through.yaml`:

```yaml
id: notifications.dnd.urgent_breaks_through
prd: [principle.1, 4.2.proactive-notifications]
status: shipped
tags: [dnd, urgency]
steps:
  - dnd: on
  - user: "Remind me in 10 minutes to take my medication. It's urgent."
  - advance_trigger: {}
expect:
  - trigger_created: {type: time, urgency: urgent}
  - notification: {source: trigger-engine, urgency: urgent, after_step: 2}
```

`evals/suites/notifications/dnd_released_when_off.yaml`:

```yaml
id: notifications.dnd.released_when_off
prd: [principle.1, 4.2.notification-delivery]
status: shipped
tags: [dnd]
steps:
  - dnd: on
  - user: "Remind me in 10 minutes to call the vet."
  - advance_trigger: {}
  - dnd: off
    settle: 5  # the drain runs in the conscious process, over the actions stream
expect:
  - notification: {source: trigger-engine, after_step: 3}
```

- [ ] **Step 3: Move the notification rows in `evals/coverage.yaml`**

```yaml
  - {id: principle.1, section: "3", prd: "Proactive, not intrusive.", suites: [notifications], pending_suites: [memory]}
  - {id: 4.2.proactive-notifications, section: "4.2", prd: "Proactive notifications with urgency levels", suites: [notifications]}
  - {id: 4.2.notification-delivery, section: "4.2", prd: "Delivery to Signal, web", suites: [notifications], tests: [tests/core/notifications]}
  - {id: 7.proactivity-quality, section: "7", prd: "Proactivity quality", pending_suites: [memory]}
```

- [ ] **Step 4: Run the loader, coverage and CLI tests**

Run: `.venv/bin/python -m pytest tests/evals/test_goldens_load.py tests/evals/test_prd_coverage.py tests/evals/test_cli.py -q`
Expected: PASS. Then `.venv/bin/alfred evals list notifications` lists 5 goldens.

- [ ] **Step 5: Commit**

```bash
git add evals/suites/notifications evals/coverage.yaml tests/evals/test_goldens_load.py
git commit -m "feat(evals): the notifications suite: urgency, do-not-disturb and its release"
```

---

### Task 14: `docs/evals.md`

**Files:**
- Modify: `docs/evals.md`

- [ ] **Step 1: Update the sections below**

Keep the file's voice: short paragraphs and tables, no marketing.

- **`## Architecture`.** After the paragraph on the LLM proxy, add a subsection `### The bus`:

  > The driver reads and acts on the eval container through `evals/harness/bus.py`.
  >
  > - **Reads:**
  >   - triggers created and fired, from `alfred:events`;
  >   - notifications, from the dispatch stream and the deferred list;
  >   - Reflex's tools, the registry's reflex-audience ones.
  > - **Acts:**
  >   - pulls a trigger's `run_at` to now (`advance_trigger`);
  >   - sets do-not-disturb through `POST /api/admin/dnd`, with an admin session minted for that one call;
  >   - sets the stored user timezone (`clock`).
  >
  > `collect.py` turns bus entries into evidence. `reflex.py` turns System 1's recorded replies into Reflex's decisions, with Reflex's own `parse_decision` and the same tools its prompt showed.
  >
  > After each sample the driver deletes the triggers that sample created. If the sample touched do-not-disturb, it clears it and drops what it held. It also puts the user timezone back. A sample that failed is not cleaned up: its stack is dirty and restarts.

- **`### One sample`.** Add a numbered item after the restore:

  > A golden with a reflex check, or a `prompt_not_contains` on `system1`, waits 6 s first. Reflex ignores an entity for 5 s after it fires.

- **`### Steps`.** Add rows to the steps table:

  | Step | What it does |
  |---|---|
  | `clock: {hour: H}` | Sets the user's zone to the `Etc/GMT±N` zone whose local hour is H now. Reflex's clock is UTC shown in that zone. Never runs in an hour's last two minutes; the reflex checks error if the prompt showed another hour. |
  | `advance_trigger: {name: …}` | Makes the newest one-time trigger the sample created (optionally narrowed by name) due now, then waits up to 15 s (`settle`) for it to fire. With no such trigger, the transcript says so and the checks score it. |
  | `dnd: on` / `dnd: off` | Sets do-not-disturb through the admin API, then settles 3 s. Turning it off drains what it held. |

- **`### Step indexes`.** Add:

  > `at_step` (reflex checks and the `reflex_ms`/`reminder_fire_ms` latencies) counts every step, like `after_step`. It defaults to the golden's last `ha_event` (reflex) or `advance_trigger` (reminders). The loader rejects an `at_step` that names a step of the wrong kind.

- **`## Checks`.** Add rows to the checks table:

  | Check | Passes when |
  |---|---|
  | `reflex_decision` | Every System 1 call in the step's window has a decision in the set, and, for act/ask, the `tool` and `target` (entity or area id) given. `none` also passes when System 1 was not called. |
  | `reflex_not_proposed` | No call in the window proposes that tool (on that target). |
  | `prompt_not_contains` | No prompt of the role contains the text; fails when the role was never called. |
  | `trigger_created` | A trigger System 2 created matches `type`, `name`, `conditions`, `run_in_seconds` (from creation to `run_at`), `at_local` (the `run_at`'s wall-clock time in a zone), `urgency` and `one_shot`. |
  | `trigger_not_created` | No trigger (of that type) was created. |
  | `trigger_fired` | A trigger created in this sample fired, within `within_s` of `after_step`'s start. |
  | `notification` | A notification was dispatched (after `after_step`), or with `deferred: true` is still held by do-not-disturb, matching `urgency`, `source` and `text`. |

  Also add `reflex_ms` and `reminder_fire_ms` to the `latency` row.

- **Matching paragraph.** Add: "A mapping matches when each key is present with a matching value, and other keys are ignored. `{regex: p}` must match the whole trimmed string, case-insensitively. A regex that does not compile fails at load."
- **`## Key paths`.** Add `bus.py`, `collect.py`, `reflex.py` and the three suite directories.
- **`## Slices still to come`.** Remove slice 2. The slice-3 line no longer mentions `prompt_not_contains`.

- [ ] **Step 2: Check the docs build nothing broken**

Run: `grep -n "alfred-evals" docs/evals.md`
Expected: no output. The command is `alfred evals`.

- [ ] **Step 3: Commit**

```bash
git add docs/evals.md
git commit -m "docs(evals): slice 2: the bus, the new steps and checks, the three suites"
```

---

### Task 15: Real runs, triage and the PR

Nothing here is code until a run shows a harness bug. A harness bug is fixed TDD-first: a failing test, the fix, the suite. An Alfred gap becomes an issue, not a change to a golden.

- [ ] **Step 1: The full gate**

Run:

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy alfredctl bus core domains evals runner sdk shared telemetry alfred_cli && .venv/bin/python -m pytest -q -p no:randomly
```

Expected: all green. Record the pytest tail.

- [ ] **Step 2: Bring the eval home-service checkout up to date**

The eval image builds home-service from `~/code/.worktrees/home-service/evals-main`, which still sits at a6f0376. It needs `origin/main`, which has rooms in live state (#26):

```bash
git -C ~/code/.worktrees/home-service/evals-main fetch origin main
git -C ~/code/.worktrees/home-service/evals-main merge --ff-only origin/main
git -C ~/code/.worktrees/home-service/evals-main log --oneline -1
```

Expected: the last line is the merge of home-service #26 or later.

- [ ] **Step 3: Run the suites**

There are two shared-vLLM bounds. The proxy holds at most 2 upstream requests and the judge at most 2. Run one suite at a time, in the background, logging to a file; wait on it with one Monitor, not a sleep loop.

```bash
.venv/bin/alfred evals run reflex --epochs 5        # run A
.venv/bin/alfred evals run reflex --epochs 5        # run B
.venv/bin/alfred evals run triggers
.venv/bin/alfred evals run notifications
.venv/bin/alfred evals run home_control conversation   # slice 1 regression
```

Expected for each run:
- a scorecard;
- no `E` except where a sample's error names an upstream vLLM failure.

Any other `E` is a harness bug: go to Step 4.

- [ ] **Step 4: Triage every `I` and `E`**

Read the sample's evidence and check reasons in Inspect's viewer (LAN :7575).

- **Harness bug:** write the failing test in the owning test file, fix it, and run `.venv/bin/python -m pytest tests/evals -q`. Commit `fix(evals): …`, then re-run that suite.
- **Alfred gap, confirmed on at least two samples:** file an issue. Give it:
  - the golden id;
  - the pass rate;
  - the System 1 or System 2 output, quoted from the evidence;
  - a link to this PR.

  The golden stays as it is. An expectation that turns out to be wrong is a golden fix, made in its own commit with the reason in the message.
- **`reflex.latency.lamp_event`:** this measures System 1 on the shared vLLM against the PRD's 500 ms. A miss is a finding to report with its numbers, not a golden to loosen.

- [ ] **Step 5: Open the PR**

```bash
git push -u origin feat/prd-evals-slice2
gh pr create --title "feat(evals): PRD eval suite slice 2: reflex, triggers, notifications" --body-file /tmp/slice2-pr.md
```

Write the body in `/tmp/slice2-pr.md` with these sections:
- **Summary:** what the slice adds.
- **Decisions:** this plan's "Decisions this plan makes", one line each.
- **Scorecards:** the five runs' scorecards, pasted verbatim.
- **Pass rates:** reflex run A against run B, per golden.
- **Findings:** the issues filed in Step 4.
- **Test plan:** the gate output tail.

End the body with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. Before pushing, check every commit with the secret-hygiene greps from `~/code/alfred-deploy/PWA-EXPOSURE-RUNBOOK.md` §Secret hygiene. Do not quote them anywhere.

- [ ] **Step 6: Post the runs**

- **On #298:** both reflex scorecards and the per-golden pass rates side by side, pasted as output, not summarised. Then a line on which acceptance criteria the PR meets.
- **On #285:** one line pointing at the PR and #298 for the judgment evals.
