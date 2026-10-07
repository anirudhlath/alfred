# Reflex Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reflex sees the event in one line plus the house by room in about 1,000 tokens instead of ~14,400, and records what it would do (act, ask, none, invalid) without executing anything.

**Architecture:** A pure prompt builder (`core/reflex/prompt.py`) renders rules and tools, preferences, "Now", the house grouped by `attributes.area`, and the change, ordered from stable to volatile so vLLM's prefix cache holds. A parser (`core/reflex/decision.py`) turns the model's reply into a `ReflexProposal` (new schema on `ReflexObservation`). The engine returns proposals; the runner and the TriggerFired loop count every decision and publish act/ask/invalid proposals as observations, and execute nothing. A `shadow_report` module prints a week of proposals for the owner's verdicts.

**Tech Stack:** Python 3.13, pydantic v2, redis-py asyncio, pytest + pytest-asyncio (`asyncio_mode = "auto"`), ruff, mypy `--strict`, `uv`.

**Spec:** `docs/superpowers/specs/2026-10-07-reflex-context-design.md` (issue #285). Rooms come from alfred-home-service#25; this slice ships without it (everything renders under "Other" until it deploys).

## Global Constraints

- Work in `~/code/.worktrees/alfred/reflex-context` on branch `feat/reflex-context`, created from `docs/reflex-context-spec` (it carries the spec and this plan). Never `cd` to `~/code/alfred-deploy/alfred`.
- Run everything through `uv run`. Per-step tests: `PYTHONDONTWRITEBYTECODE=1 uv run pytest <paths> -q -p no:randomly`. Full gate once, in Task 9.
- ruff: line length 100, rules `E W F I N UP B A SIM TCH RUF`. mypy `--strict` over `alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/` — that includes `core/**/tests` and `bus/**/tests`; `tests/` is not type-checked but keep it typed.
- **Public repo:** no household names, emails, hostnames or IPs in code, tests, fixtures, docs or commit messages. Use placeholders ("Person A", "Living Room", "A Film").
- **Nothing executes in this slice:** no Reflex path may call `execute_action`. `DomainRouter` stays wired and the `agent` / `result_stream` parameters stay (unused) for #286.
- Tools come only from `ToolRegistry`; production code never hardcodes tool names. Only `audience == "reflex"` tools reach the prompt (contract C9).
- The SDK (`sdk/alfred_sdk/`) does not change. Rooms arrive as `attributes.area`.
- `HOUSE_DOMAINS = {light, media_player, switch, climate, fan, cover, lock, vacuum}`. Time of day: morning 05–12, afternoon 12–17, evening 17–21, night 21–05.
- OpenAI-compatible backend: `max_tokens=150`; `temperature=0.0` and `response_format={"type": "json_object"}` unchanged.
- Decision counters: hash `alfred:reflex:decisions:<YYYY-MM-DD>` (UTC date), one field per decision, TTL 2,592,000 s (30 days).
- The 5-minute context cache removed by #283 is **not** restored.
- Never call the cloud LLM from the reflex path.
- Before every commit: `uv run ruff format <changed files> && uv run ruff check --fix <changed files>` (the plan's code is not pre-wrapped to 100 columns).
- Commit subjects are conventional (`feat(reflex): …`, `test(reflex): …`, `docs(reflex): …`) and reference #285. No model identifiers in commits.

## Review Focus

1. A model reply wrapped in a code fence or followed by prose → an `invalid` proposal that keeps the raw text; never a crash and never an act. *(Task 2: `test_a_reply_wrapped_in_extra_text_is_invalid_not_a_crash`)*
2. The pre-#285 reply shape `{"tool_name": …}` with no `decision` → `invalid`, not treated as act. *(Task 2: `test_the_pre_285_action_shape_is_invalid_not_executed`)*
3. Live-state attributes of unexpected types or blank (non-string name, blank or non-string area, string or boolean brightness, missing temperatures, `None` title) → the House renders without raising and blank rooms go to "Other". *(Task 4: `test_house_tolerates_odd_attribute_values`)*
4. Very long values (a 500-character media title, a huge or non-JSON trigger context) → clipped so one event cannot bloat the prompt. *(Task 4: `test_long_titles_are_clipped`, `test_trigger_context_that_is_not_json_or_is_huge_still_renders`)*
5. An empty or malformed timezone name → "Now" falls back to UTC instead of raising. *(Task 4: `test_a_bad_timezone_falls_back_to_utc`)*

---

### Task 1: `ReflexProposal` on `ReflexObservation`

**Files:**
- Modify: `bus/schemas/events.py` (insert before `class ReflexObservation`, add one field to it)
- Test: `bus/schemas/tests/test_reflex_proposal.py` (create)
- Test: `tests/core/memory/test_ingestor_proposal.py` (create)

**Interfaces:**
- Produces: `bus.schemas.events.ReflexDecision = Literal["act", "ask", "none", "invalid"]`; `ReflexProposal(decision, reason: str | None = None, action: ActionRequest | None = None, raw: str | None = None, problem: str | None = None)`; `ReflexObservation.proposal: ReflexProposal | None = None`.

- [ ] **Step 0: Branch and baseline**

```bash
cd ~/code/.worktrees/alfred/reflex-context
git switch -c feat/reflex-context
uv venv --python 3.13   # worktrees otherwise pick up the system Python
uv sync --all-extras   # pytest, ruff and mypy live in the dev extra
PYTHONDONTWRITEBYTECODE=1 uv run pytest core/reflex tests/core/reflex bus/schemas -q -p no:randomly
```
Expected: all pass. If anything fails, stop and report it before changing code.

- [ ] **Step 1: Write the failing schema tests**

Create `bus/schemas/tests/test_reflex_proposal.py`:

```python
"""ReflexProposal — Reflex's shadow decision, carried on ReflexObservation (#285)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bus.schemas.events import ActionRequest, ReflexObservation, ReflexProposal


def _act() -> ReflexProposal:
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


def test_a_proposal_round_trips_inside_an_observation() -> None:
    obs = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event={"entity_id": "media_player.living_room_tv"},
        proposal=_act(),
    )

    back = ReflexObservation.model_validate_json(obs.model_dump_json())

    assert back.proposal == obs.proposal
    # A proposal is not an action: the "this happened" fields stay empty.
    assert back.action is None
    assert back.result is None


def test_an_observation_written_before_proposals_still_parses() -> None:
    legacy = (
        '{"event_type": "reflex_observation", "source": "reflex-engine",'
        ' "origin": "state_change", "trigger_event": {}}'
    )

    assert ReflexObservation.model_validate_json(legacy).proposal is None


def test_an_unknown_decision_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ReflexProposal.model_validate({"decision": "maybe"})
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run pytest bus/schemas/tests/test_reflex_proposal.py -q -p no:randomly`
Expected: FAIL — `ImportError: cannot import name 'ReflexProposal'`.

- [ ] **Step 3: Add the schema**

In `bus/schemas/events.py`, insert directly above `class ReflexObservation(BaseEvent):`:

```python
ReflexDecision = Literal["act", "ask", "none", "invalid"]


class ReflexProposal(BaseModel):
    """What the Reflex Engine decided about one event (#285).

    In shadow mode nothing executes: ``action`` is what Reflex *would* have run.
    ``raw`` keeps the model's text and ``problem`` says what was wrong with it when
    the decision is ``invalid``.
    """

    decision: ReflexDecision
    reason: str | None = None
    action: ActionRequest | None = None
    raw: str | None = None
    problem: str | None = None
```

In `class ReflexObservation`, after `decision_context: str | None = None`, add:

```python
    # Reflex's decision when it was act, ask or invalid (#285). action/result above
    # mean "this happened"; a proposal only means "Reflex would have done this".
    proposal: ReflexProposal | None = None
```

- [ ] **Step 4: Run it to make sure it passes**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run pytest bus/schemas/tests/test_reflex_proposal.py -q -p no:randomly`
Expected: 3 passed.

- [ ] **Step 5: Pin that memory ignores proposals**

Create `tests/core/memory/test_ingestor_proposal.py`. It passes as soon as the schema exists; it guards behaviour that must not change (spec §4, "Memory"):

```python
"""A Reflex proposal never changes what episodic memory records (#285)."""

from __future__ import annotations

from bus.schemas.events import ReflexObservation, ReflexProposal, StateChangedEvent
from core.memory.ingestor import _build_observation_semantic_key, _build_observation_summary


def test_a_proposal_does_not_change_the_memory_text() -> None:
    event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="media_player.living_room_tv",
        old_state="paused",
        new_state="playing",
    )
    plain = ReflexObservation(
        source="reflex-engine", origin="state_change", trigger_event=event.model_dump()
    )
    proposed = plain.model_copy(
        update={"proposal": ReflexProposal(decision="ask", reason="Film at night")}
    )
    stamp = "Wed 2026-10-07 22:30"

    assert _build_observation_summary(proposed, stamp) == _build_observation_summary(plain, stamp)
    assert _build_observation_semantic_key(proposed) == _build_observation_semantic_key(plain)
```

Run: `PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/core/memory/test_ingestor_proposal.py -q -p no:randomly`
Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add bus/schemas/events.py bus/schemas/tests/test_reflex_proposal.py tests/core/memory/test_ingestor_proposal.py
git commit -m "feat(reflex): ReflexProposal on ReflexObservation for shadow decisions (#285)"
```

---

### Task 2: Parse the model's reply into a `ReflexProposal`

**Files:**
- Create: `core/reflex/decision.py`
- Test: `tests/core/reflex/test_decision.py`

**Interfaces:**
- Consumes: `ReflexProposal`, `ActionRequest` (Task 1); `core.reflex.tool_registry.ToolInfo` (existing: `name`, `target_service`, `parameters`, `audience`).
- Produces: `parse_decision(raw: str, tools: Sequence[ToolInfo]) -> ReflexProposal`. `tools` is exactly the list the prompt showed (Reflex-audience tools). Problems are these exact strings: `"not JSON"`, `"not a JSON object"`, `"unknown decision {decision!r}"`, `"{decision} without a tool"`, `"tool {name!r} is not one of Reflex's tools"`, `"target_service {svc!r} does not serve {tool}"`, `"parameters is not an object"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/reflex/test_decision.py`:

```python
"""parse_decision — the model's reply as a ReflexProposal (#285)."""

from __future__ import annotations

import json

import pytest

from core.reflex.decision import parse_decision
from core.reflex.tool_registry import ToolInfo

TOOLS = [
    ToolInfo(
        name="home.light_turn_on",
        description="light.turn_on",
        parameters={"target": {"type": "str"}, "brightness_pct": {"type": "float"}},
        feature_name="home",
        feature_description="",
        target_service="home-service",
        audience="reflex",
    )
]


def _act(**overrides: object) -> str:
    reply: dict[str, object] = {
        "decision": "act",
        "reason": "Film at night",
        "tool_name": "home.light_turn_on",
        "target_service": "home-service",
        "parameters": {"target": "Living Room", "brightness_pct": 30},
    }
    reply.update(overrides)
    return json.dumps(reply)


def test_none_is_none() -> None:
    proposal = parse_decision('{"decision": "none"}', TOOLS)

    assert proposal.decision == "none"
    assert proposal.action is None
    assert proposal.raw is None


def test_the_pre_285_none_shape_still_reads_as_none() -> None:
    assert parse_decision('{"action": "none"}', TOOLS).decision == "none"


@pytest.mark.parametrize("decision", ["act", "ask"])
def test_act_and_ask_carry_the_action_and_its_reason(decision: str) -> None:
    proposal = parse_decision(_act(decision=decision), TOOLS)

    assert proposal.decision == decision
    assert proposal.reason == "Film at night"
    assert proposal.action is not None
    assert proposal.action.source == "reflex-engine"
    assert proposal.action.tool_name == "home.light_turn_on"
    assert proposal.action.target_service == "home-service"
    assert proposal.action.parameters == {"target": "Living Room", "brightness_pct": 30}
    assert proposal.action.reason == "Film at night"


def test_a_missing_target_service_is_taken_from_the_tool() -> None:
    reply = json.loads(_act())
    del reply["target_service"]

    proposal = parse_decision(json.dumps(reply), TOOLS)

    assert proposal.decision == "act"
    assert proposal.action is not None
    assert proposal.action.target_service == "home-service"


def test_a_missing_reason_is_allowed() -> None:
    reply = json.loads(_act())
    del reply["reason"]

    proposal = parse_decision(json.dumps(reply), TOOLS)

    assert proposal.decision == "act"
    assert proposal.reason is None


@pytest.mark.parametrize(
    ("raw", "problem"),
    [
        ("not json", "not JSON"),
        ("", "not JSON"),
        ('["act"]', "not a JSON object"),
        ('{"decision": "maybe"}', "unknown decision 'maybe'"),
        ('{"decision": "act", "reason": "x"}', "act without a tool"),
        (
            _act(tool_name="home.lock_unlock"),
            "tool 'home.lock_unlock' is not one of Reflex's tools",
        ),
        (
            _act(target_service="other-service"),
            "target_service 'other-service' does not serve home.light_turn_on",
        ),
        (_act(parameters=["Living Room"]), "parameters is not an object"),
    ],
)
def test_anything_else_is_invalid_and_keeps_the_raw_text(raw: str, problem: str) -> None:
    proposal = parse_decision(raw, TOOLS)

    assert proposal.decision == "invalid"
    assert proposal.problem == problem
    assert proposal.raw == raw
    assert proposal.action is None


# Review Focus 1
@pytest.mark.parametrize(
    "raw",
    [
        "```json\n" + _act() + "\n```",
        _act() + "\nThat should help.",
    ],
)
def test_a_reply_wrapped_in_extra_text_is_invalid_not_a_crash(raw: str) -> None:
    proposal = parse_decision(raw, TOOLS)

    assert proposal.decision == "invalid"
    assert proposal.problem == "not JSON"
    assert proposal.raw == raw


# Review Focus 2
def test_the_pre_285_action_shape_is_invalid_not_executed() -> None:
    legacy = json.dumps(
        {"tool_name": "home.light_turn_on", "target_service": "home-service", "parameters": {}}
    )

    proposal = parse_decision(legacy, TOOLS)

    assert proposal.decision == "invalid"
    assert proposal.problem == "unknown decision None"
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/core/reflex/test_decision.py -q -p no:randomly`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.reflex.decision'`.

- [ ] **Step 3: Implement the parser**

Create `core/reflex/decision.py`:

```python
"""Parse the Reflex model's reply into a ReflexProposal (#285).

The model answers ``{"decision": "none"}`` or ``{"decision": "act" | "ask", "reason",
"tool_name", "target_service", "parameters"}``. Anything this module cannot turn into
one of those becomes an ``invalid`` proposal that keeps the raw text and says what was
wrong, so the shadow report shows model failures instead of hiding them.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from bus.schemas.events import ActionRequest, ReflexProposal

if TYPE_CHECKING:
    from collections.abc import Sequence

    from core.reflex.tool_registry import ToolInfo


def _invalid(raw: str, problem: str) -> ReflexProposal:
    return ReflexProposal(decision="invalid", raw=raw, problem=problem)


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def parse_decision(raw: str, tools: Sequence[ToolInfo]) -> ReflexProposal:
    """Turn the model's reply into a proposal, validated against Reflex's own tools.

    ``tools`` is the list the prompt showed. An act or ask naming any other tool is
    invalid, which keeps contract C9's audience rule in shadow mode too.
    """
    try:
        parsed: Any = json.loads(raw)
    except json.JSONDecodeError:
        return _invalid(raw, "not JSON")
    if not isinstance(parsed, dict):
        return _invalid(raw, "not a JSON object")

    decision = parsed.get("decision")
    if decision is None and parsed.get("action") == "none":
        return ReflexProposal(decision="none")  # the pre-#285 reply shape
    if decision == "none":
        return ReflexProposal(decision="none", reason=_text(parsed.get("reason")))
    if decision not in ("act", "ask"):
        return _invalid(raw, f"unknown decision {decision!r}")

    tool_name = _text(parsed.get("tool_name"))
    if tool_name is None:
        return _invalid(raw, f"{decision} without a tool")
    tool = next((t for t in tools if t.name == tool_name), None)
    if tool is None:
        return _invalid(raw, f"tool {tool_name!r} is not one of Reflex's tools")
    target_service = parsed.get("target_service", tool.target_service)
    if target_service != tool.target_service:
        return _invalid(raw, f"target_service {target_service!r} does not serve {tool_name}")
    parameters = parsed.get("parameters", {})
    if not isinstance(parameters, dict):
        return _invalid(raw, "parameters is not an object")

    reason = _text(parsed.get("reason"))
    return ReflexProposal(
        decision=decision,
        reason=reason,
        action=ActionRequest(
            source="reflex-engine",
            target_service=tool.target_service,
            tool_name=tool.name,
            parameters=parameters,
            reason=reason,
        ),
    )
```

- [ ] **Step 4: Run the tests to make sure they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/core/reflex/test_decision.py -q -p no:randomly && uv run mypy --strict core/reflex/decision.py`
Expected: all passed; mypy `Success`.

- [ ] **Step 5: Commit**

```bash
git add core/reflex/decision.py tests/core/reflex/test_decision.py
git commit -m "feat(reflex): parse model replies into act/ask/none/invalid proposals (#285)"
```

---

### Task 3: Bound the model's output

**Files:**
- Modify: `core/reflex/openai_client.py` (the request body in `infer`)
- Test: `tests/core/reflex/test_inference_backends.py` (`test_openai_infer_request_shape_and_parse`)

**Interfaces:**
- Produces: `core.reflex.openai_client.MAX_OUTPUT_TOKENS = 150`, sent as `max_tokens` on every Reflex call.

- [ ] **Step 1: Extend the existing request-shape test**

In `tests/core/reflex/test_inference_backends.py`, in `test_openai_infer_request_shape_and_parse`, after `assert body["response_format"] == {"type": "json_object"}` add:

```python
    assert body["temperature"] == 0.0
    # A "none" is ~7 tokens and a reason ~40; the cap bounds a rambling model (#285).
    assert body["max_tokens"] == 150
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/core/reflex/test_inference_backends.py::test_openai_infer_request_shape_and_parse -q -p no:randomly`
Expected: FAIL — `KeyError: 'max_tokens'`.

- [ ] **Step 3: Send the cap**

In `core/reflex/openai_client.py`, below `_http_client: httpx.AsyncClient | None = None` add:

```python
# Reflex replies are a short JSON object: ~7 tokens for "none", ~40 with a reason.
# The cap bounds the worst case at ~0.9 s of decode time (#285).
MAX_OUTPUT_TOKENS = 150
```

and in `infer`'s `json={...}` body add `"max_tokens": MAX_OUTPUT_TOKENS,` after `"temperature": 0.0,`.

- [ ] **Step 4: Run it to make sure it passes**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/core/reflex/test_inference_backends.py -q -p no:randomly`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add core/reflex/openai_client.py tests/core/reflex/test_inference_backends.py
git commit -m "feat(reflex): cap Reflex output at 150 tokens (#285)"
```

---

### Task 4: Prompt sections — Now, House, What changed, Trigger fired

**Files:**
- Create: `core/reflex/prompt.py` (renderers only; Task 5 adds assembly)
- Test: `tests/core/reflex/test_prompt_sections.py`
- Modify: `docs/live-state.md` (new "Well-known attributes" section)

**Interfaces:**
- Consumes: `sdk.alfred_sdk.context.ContextSnapshot` / `ContextEntry` (existing); `core.reflex.context_reader.LIVE_STATE_UNAVAILABLE` (existing string `"Live home state unavailable."`); `StateChangedEvent`, `TriggerFired`.
- Produces (all in `core.reflex.prompt`):
  - `LiveEntity(entity_id: str, domain: str, controllable: bool, state: str, attributes: Mapping[str, Any])` — frozen dataclass, field order as written.
  - `index_snapshot(snapshot: ContextSnapshot) -> dict[str, LiveEntity]`
  - `time_of_day(hour: int) -> str`
  - `render_now(now: datetime, tz_name: str, entities: Mapping[str, LiveEntity] | None) -> str`
  - `render_house(entities: Mapping[str, LiveEntity] | None) -> str`
  - `render_event(event: StateChangedEvent, entities: Mapping[str, LiveEntity] | None) -> str`
  - `render_trigger(event: TriggerFired) -> str`
  - Constants `HOUSE_DOMAINS`, `EVENT_ATTRIBUTES`, `MAX_EVENT_DETAILS = 3`, `MAX_DETAIL_CHARS = 80`, `MAX_TRIGGER_CONTEXT_CHARS = 500`, `OTHER_ROOM = "Other"`, `NO_DEVICES = "No devices to show."`

- [ ] **Step 1: Write the failing tests**

Create `tests/core/reflex/test_prompt_sections.py`:

```python
"""Reflex prompt sections — Now, House, What changed, Trigger fired (#285)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bus.schemas.events import StateChangedEvent, TriggerFired
from core.reflex.context_reader import LIVE_STATE_UNAVAILABLE
from core.reflex.prompt import (
    MAX_DETAIL_CHARS,
    LiveEntity,
    index_snapshot,
    render_event,
    render_house,
    render_now,
    render_trigger,
    time_of_day,
)
from sdk.alfred_sdk.context import ContextEntry, ContextSnapshot

# 03:30 UTC on Thu 8 Oct 2026 is 22:30 on Wed 7 Oct in Chicago (CDT, UTC-5).
NIGHT_UTC = datetime(2026, 10, 8, 3, 30, tzinfo=UTC)


def _e(entity_id: str, state: str, controllable: bool = True, **attributes: object) -> LiveEntity:
    return LiveEntity(
        entity_id=entity_id,
        domain=entity_id.split(".", 1)[0],
        controllable=controllable,
        state=state,
        attributes=attributes,
    )


def _index(*entities: LiveEntity) -> dict[str, LiveEntity]:
    return {e.entity_id: e for e in entities}


def _change(entity_id: str, old: str | None, new: str, **attributes: object) -> StateChangedEvent:
    return StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id=entity_id,
        old_state=old,
        new_state=new,
        attributes=attributes,
    )


# --- index_snapshot ------------------------------------------------------------


def test_index_snapshot_flattens_both_buckets() -> None:
    snapshot = ContextSnapshot(
        controllable={
            "light": [
                ContextEntry(entity_id="light.tv_lamp", state="on", attributes={"area": "Den"})
            ]
        },
        sensors={"sun": [ContextEntry(entity_id="sun.sun", state="below_horizon")]},
    )

    index = index_snapshot(snapshot)

    assert index["light.tv_lamp"] == LiveEntity("light.tv_lamp", "light", True, "on", {"area": "Den"})
    assert index["sun.sun"] == LiveEntity("sun.sun", "sun", False, "below_horizon", {})


# --- Now -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hour", "label"),
    [
        (0, "night"),
        (4, "night"),
        (5, "morning"),
        (11, "morning"),
        (12, "afternoon"),
        (16, "afternoon"),
        (17, "evening"),
        (20, "evening"),
        (21, "night"),
        (23, "night"),
    ],
)
def test_time_of_day_boundaries(hour: int, label: str) -> None:
    assert time_of_day(hour) == label


def test_now_is_local_with_sun_and_people_sorted_by_name() -> None:
    entities = _index(
        _e("sun.sun", "below_horizon", controllable=False),
        _e("person.b", "Work", friendly_name="Person B"),
        _e("person.a", "home", friendly_name="Person A"),
    )

    assert render_now(NIGHT_UTC, "America/Chicago", entities) == (
        "Wed 7 Oct, 22:30 (night) · sun down\nPeople: Person A home · Person B Work"
    )


def test_now_without_live_state_is_just_the_clock() -> None:
    assert render_now(NIGHT_UTC, "UTC", None) == "Thu 8 Oct, 03:30 (night)"


def test_now_with_no_people_or_sun_omits_them() -> None:
    assert render_now(NIGHT_UTC, "UTC", {}) == "Thu 8 Oct, 03:30 (night)"


# Review Focus 5
@pytest.mark.parametrize("tz_name", ["", "Not/AZone", "../etc/passwd"])
def test_a_bad_timezone_falls_back_to_utc(tz_name: str) -> None:
    assert render_now(NIGHT_UTC, tz_name, None) == "Thu 8 Oct, 03:30 (night)"


# --- House ---------------------------------------------------------------------


def test_house_groups_by_room_sorted_with_other_last() -> None:
    entities = _index(
        _e("light.desk_left", "on", friendly_name="Desk Left", area="Office", brightness=178),
        _e("light.tv_lamp", "off", friendly_name="TV Lamp", area="Living Room"),
        _e(
            "media_player.living_room_tv",
            "playing",
            friendly_name="Living Room TV",
            area="Living Room",
            media_title="A Film",
        ),
        _e("climate.thermostat", "heat", friendly_name="Thermostat", temperature=68, current_temperature=66.5),
        _e("vacuum.robot", "docked", friendly_name="Robot"),
    )

    assert render_house(entities) == (
        'Living Room: Living Room TV playing "A Film" · TV Lamp off\n'
        "Office: Desk Left on 70%\n"
        "Other: Robot docked · Thermostat heat 68° (now 66.5°)"
    )


def test_house_leaves_out_sensors_people_and_noise_domains() -> None:
    entities = _index(
        _e("light.tv_lamp", "off", friendly_name="TV Lamp", area="Living Room"),
        _e("button.identify", "unknown", friendly_name="Identify"),
        _e("notify.phone", "unknown", friendly_name="Phone"),
        _e("sensor.power", "12", controllable=False, friendly_name="Power"),
        _e("person.a", "home", friendly_name="Person A"),
        _e("light.reported_as_sensor", "on", controllable=False, friendly_name="Ghost"),
    )

    assert render_house(entities) == "Living Room: TV Lamp off"


def test_house_without_live_state_says_so() -> None:
    assert render_house(None) == LIVE_STATE_UNAVAILABLE


def test_house_with_nothing_to_show_says_so() -> None:
    assert render_house({}) == "No devices to show."


# Review Focus 3
def test_house_tolerates_odd_attribute_values() -> None:
    entities = _index(
        _e("light.a", "on", friendly_name="Lamp A", area="", brightness="bright"),
        _e("light.b", "on", area=None, brightness=None),
        _e("light.c", "on", friendly_name=42, area=7, brightness=True),
        _e("climate.t", "heat", friendly_name="Thermostat", temperature=None, current_temperature="warm"),
        _e("media_player.tv", "playing", friendly_name="TV", media_title=None),
    )

    assert render_house(entities) == (
        "Other: Lamp A on · light.b on · light.c on · Thermostat heat · TV playing"
    )


# Review Focus 4
def test_long_titles_are_clipped() -> None:
    entities = _index(
        _e("media_player.tv", "playing", friendly_name="TV", area="Den", media_title="x" * 500)
    )

    line = render_house(entities)

    assert len(line) < 2 * MAX_DETAIL_CHARS
    assert line.endswith('…"')


# --- What changed --------------------------------------------------------------


def test_event_line_uses_live_name_and_room_and_drops_raw_attributes() -> None:
    event = _change(
        "media_player.living_room_tv",
        "paused",
        "playing",
        friendly_name="Old Name",
        media_title="A Film",
        app_name="Streamer",
        entity_picture="/api/media_player_proxy/x?token=abc",
        supported_features=450487,
    )
    entities = _index(
        _e("media_player.living_room_tv", "playing", friendly_name="Living Room TV", area="Living Room")
    )

    assert render_event(event, entities) == (
        'Living Room TV (Living Room): paused → playing · "A Film" · Streamer'
    )


def test_event_details_are_capped_at_three() -> None:
    event = _change(
        "media_player.kitchen_speaker",
        "idle",
        "playing",
        friendly_name="Kitchen Speaker",
        media_title="Song",
        media_artist="Band",
        app_name="Radio",
        brightness=100,
    )

    assert render_event(event, None) == 'Kitchen Speaker: idle → playing · "Song" · Band · Radio'


def test_climate_event_details() -> None:
    event = _change(
        "climate.thermostat",
        "off",
        "heat",
        friendly_name="Thermostat",
        temperature=68,
        current_temperature=66,
        hvac_action="heating",
    )

    assert render_event(event, None) == "Thermostat: off → heat · set 68° · now 66° · heating"


def test_an_entity_missing_from_live_state_uses_the_event() -> None:
    event = _change("binary_sensor.front_door", "off", "on", device_class="door")

    assert render_event(event, {}) == "binary_sensor.front_door: off → on"


def test_a_missing_old_state_is_shown_as_none() -> None:
    event = _change("person.a", None, "home", friendly_name="Person A")

    assert render_event(event, None) == "Person A: (none) → home"


# --- Trigger fired -------------------------------------------------------------


def test_trigger_line_with_context() -> None:
    event = TriggerFired(
        trigger_id="t-1",
        trigger_name="bedtime",
        trigger_type="time",
        context={"evaluated_at": "22:30", "b": 1},
    )

    assert render_trigger(event) == 'bedtime (time)\nContext: {"b": 1, "evaluated_at": "22:30"}'


def test_trigger_without_context_is_one_line() -> None:
    event = TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time")

    assert render_trigger(event) == "bedtime (time)"


# Review Focus 4
def test_trigger_context_that_is_not_json_or_is_huge_still_renders() -> None:
    event = TriggerFired(
        trigger_id="t-1",
        trigger_name="bedtime",
        trigger_type="time",
        context={"at": NIGHT_UTC, "blob": "y" * 5000},
    )

    text = render_trigger(event)

    assert text.startswith('bedtime (time)\nContext: {"at": "2026-10-08 03:30:00+00:00"')
    assert len(text) < 700
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/core/reflex/test_prompt_sections.py -q -p no:randomly`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.reflex.prompt'`.

- [ ] **Step 3: Implement the renderers**

Create `core/reflex/prompt.py`:

```python
"""Reflex prompt: what the Reflex Engine sees for one event (#285).

Pure: live state, the event and the clock go in, a string comes out, with no I/O.
The sections run from the most stable to the least so vLLM's prefix cache covers as
much as it can: rules and tools, preferences, now, the house by room, then the change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from core.reflex.context_reader import LIVE_STATE_UNAVAILABLE

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from bus.schemas.events import StateChangedEvent, TriggerFired
    from sdk.alfred_sdk.context import ContextSnapshot

# Domains the House section lists. A rendering choice, not a tool list: Reflex acts only
# through its registered tools, but it judges better for seeing the thermostat.
HOUSE_DOMAINS: frozenset[str] = frozenset(
    {"light", "media_player", "switch", "climate", "fan", "cover", "lock", "vacuum"}
)
# Event attributes worth a word on the What changed line, in display order.
EVENT_ATTRIBUTES: tuple[str, ...] = (
    "media_title",
    "media_artist",
    "app_name",
    "brightness",
    "temperature",
    "current_temperature",
    "hvac_action",
)
MAX_EVENT_DETAILS = 3
# One long name, title or trigger context must not bloat the prompt.
MAX_DETAIL_CHARS = 80
MAX_TRIGGER_CONTEXT_CHARS = 500
OTHER_ROOM = "Other"
NO_DEVICES = "No devices to show."


@dataclass(frozen=True)
class LiveEntity:
    """One live-state entry, lifted out of its snapshot bucket."""

    entity_id: str
    domain: str
    controllable: bool
    state: str
    attributes: Mapping[str, Any]


def index_snapshot(snapshot: ContextSnapshot) -> dict[str, LiveEntity]:
    """Every entry of both buckets, keyed by entity ID."""
    index: dict[str, LiveEntity] = {}
    for controllable, bucket in ((True, snapshot.controllable), (False, snapshot.sensors)):
        for domain, entries in bucket.items():
            for entry in entries:
                index[entry.entity_id] = LiveEntity(
                    entity_id=entry.entity_id,
                    domain=domain,
                    controllable=controllable,
                    state=entry.state,
                    attributes=entry.attributes,
                )
    return index


def _clip(value: object, limit: int = MAX_DETAIL_CHARS) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _name(entity_id: str, attributes: Mapping[str, Any]) -> str:
    name = attributes.get("friendly_name")
    return _clip(name) if isinstance(name, str) and name.strip() else entity_id


def _area(attributes: Mapping[str, Any]) -> str | None:
    area = attributes.get("area")
    return _clip(area) if isinstance(area, str) and area.strip() else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _percent(brightness: object) -> str | None:
    value = _number(brightness)
    return None if value is None else f"{round(value / 255 * 100)}%"


def _degrees(value: object) -> str | None:
    number = _number(value)
    return None if number is None else f"{number:g}°"


def time_of_day(hour: int) -> str:
    """A coarse label for a local hour, so the model need not reason about 22:57."""
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"


def _zone(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except Exception:  # unknown, empty or path-like key
        return ZoneInfo("UTC")


def render_now(now: datetime, tz_name: str, entities: Mapping[str, LiveEntity] | None) -> str:
    """Local time and time of day, then the sun and who is where, when live state has them."""
    local = now.astimezone(_zone(tz_name))
    clock = f"{local:%a} {local.day} {local:%b}, {local:%H:%M} ({time_of_day(local.hour)})"
    if entities is None:
        return clock
    sun = entities.get("sun.sun")
    if sun is not None and sun.state in ("above_horizon", "below_horizon"):
        clock += " · sun up" if sun.state == "above_horizon" else " · sun down"
    people = sorted(
        (e for e in entities.values() if e.domain == "person"),
        key=lambda e: _name(e.entity_id, e.attributes).casefold(),
    )
    if not people:
        return clock
    who = " · ".join(f"{_name(p.entity_id, p.attributes)} {_clip(p.state)}" for p in people)
    return f"{clock}\nPeople: {who}"


def _house_detail(entity: LiveEntity) -> str | None:
    attributes = entity.attributes
    if entity.domain == "light" and entity.state == "on":
        return _percent(attributes.get("brightness"))
    if entity.domain == "media_player" and entity.state == "playing":
        title = attributes.get("media_title")
        return f'"{_clip(title)}"' if isinstance(title, str) and title.strip() else None
    if entity.domain == "climate":
        target = _degrees(attributes.get("temperature"))
        current = _degrees(attributes.get("current_temperature"))
        if target and current:
            return f"{target} (now {current})"
        if target:
            return target
        return f"now {current}" if current else None
    return None


def _house_item(entity: LiveEntity) -> str:
    text = f"{_name(entity.entity_id, entity.attributes)} {_clip(entity.state)}"
    detail = _house_detail(entity)
    return f"{text} {detail}" if detail else text


def render_house(entities: Mapping[str, LiveEntity] | None) -> str:
    """The actionable house, one line per room; entities with no room come last."""
    if entities is None:
        return LIVE_STATE_UNAVAILABLE
    rooms: dict[str, list[LiveEntity]] = {}
    for entity in entities.values():
        if entity.controllable and entity.domain in HOUSE_DOMAINS:
            rooms.setdefault(_area(entity.attributes) or OTHER_ROOM, []).append(entity)
    if not rooms:
        return NO_DEVICES
    order = sorted(room for room in rooms if room != OTHER_ROOM)
    if OTHER_ROOM in rooms:
        order.append(OTHER_ROOM)
    lines: list[str] = []
    for room in order:
        members = sorted(
            rooms[room],
            key=lambda e: (_name(e.entity_id, e.attributes).casefold(), e.entity_id),
        )
        lines.append(f"{room}: " + " · ".join(_house_item(e) for e in members))
    return "\n".join(lines)


def _event_detail(key: str, value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if key == "brightness":
        return _percent(value)
    if key == "temperature":
        degrees = _degrees(value)
        return f"set {degrees}" if degrees else None
    if key == "current_temperature":
        degrees = _degrees(value)
        return f"now {degrees}" if degrees else None
    text = _clip(value)
    if not text:
        return None
    return f'"{text}"' if key == "media_title" else text


def render_event(event: StateChangedEvent, entities: Mapping[str, LiveEntity] | None) -> str:
    """One line: name (room): old → new, then up to three details from the event."""
    known = entities.get(event.entity_id) if entities is not None else None
    # Live state names and places the entity; the event carries only HA's raw attributes.
    attributes = {**event.attributes, **(known.attributes if known is not None else {})}
    name = _name(event.entity_id, attributes)
    area = _area(attributes)
    head = f"{name} ({area})" if area else name
    old = _clip(event.old_state) if event.old_state is not None else "(none)"
    line = f"{head}: {old} → {_clip(event.new_state)}"
    details = [
        detail
        for key in EVENT_ATTRIBUTES
        if (detail := _event_detail(key, event.attributes.get(key))) is not None
    ][:MAX_EVENT_DETAILS]
    return " · ".join([line, *details])


def render_trigger(event: TriggerFired) -> str:
    """The trigger's name and type, then its context as compact JSON."""
    line = f"{_clip(event.trigger_name)} ({_clip(event.trigger_type)})"
    if not event.context:
        return line
    context = json.dumps(event.context, sort_keys=True, default=str)
    return f"{line}\nContext: {_clip(context, MAX_TRIGGER_CONTEXT_CHARS)}"
```

- [ ] **Step 4: Run the tests to make sure they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/core/reflex/test_prompt_sections.py -q -p no:randomly && uv run mypy --strict core/reflex/prompt.py`
Expected: all passed; mypy `Success`.

- [ ] **Step 5: Document the well-known attributes**

In `docs/live-state.md`, insert this section directly above `## Writing — \`LiveStateWriter(redis_url, service_name)\``:

```markdown
## Well-known attributes

`attributes` is where a service puts its own details, and the SDK never inspects it.
Alfred does understand a few keys when a service provides them. All are optional: without
them Alfred falls back to the entity ID.

| Key | Meaning | Alfred uses it for |
|---|---|---|
| `friendly_name` | The display name | Every prompt line that names the entity |
| `area` | Where the entity is, as a room or area name | Grouping Reflex's House section by room ([#285](https://github.com/anirudhlath/alfred/issues/285)); a room name is also a tool target wherever the service's tools accept one |
| `unit_of_measurement` | The unit of `state` | Rendering numbers |
| `device_class` | What kind of sensor or device it is | Attention seeding |

**How to help Alfred understand your entities.** Set `friendly_name` to what a person
would call the thing, and `area` to where it is, in the same words your tools accept as a
target. Keep everything else in `attributes` few and small: the Conscious engine renders
every attribute it is given.
```

- [ ] **Step 6: Commit**

```bash
git add core/reflex/prompt.py tests/core/reflex/test_prompt_sections.py docs/live-state.md
git commit -m "feat(reflex): render Now, the house by room and the change in one line (#285)"
```

---

### Task 5: Assemble the prompt, stable to volatile

**Files:**
- Modify: `core/reflex/prompt.py` (append assembly)
- Test: `tests/core/reflex/test_prompt_assembly.py`

**Interfaces:**
- Consumes: Task 4's renderers and `LiveEntity`; `ToolInfo`.
- Produces (in `core.reflex.prompt`):
  - `render_tools(tools: Sequence[ToolInfo]) -> str`
  - `build_state_change_prompt(*, event: StateChangedEvent, preferences: str, tools: Sequence[ToolInfo], entities: Mapping[str, LiveEntity] | None, now: datetime, tz_name: str) -> str`
  - `build_trigger_prompt(*, event: TriggerFired, preferences: str, tools: Sequence[ToolInfo], entities: Mapping[str, LiveEntity] | None, now: datetime, tz_name: str) -> str`
  - Constants `STATE_CHANGE_INTRO`, `TRIGGER_INTRO`, `DECISION_RULES`, `NO_TOOLS = "No tools available."`, `NO_PREFERENCES = "None recorded yet."`

- [ ] **Step 1: Write the failing tests**

Create `tests/core/reflex/test_prompt_assembly.py`:

```python
"""Reflex prompt assembly — order, cacheable prefix, tools, size (#285)."""

from __future__ import annotations

from datetime import UTC, datetime

from bus.schemas.events import StateChangedEvent, TriggerFired
from core.reflex.prompt import (
    LiveEntity,
    build_state_change_prompt,
    build_trigger_prompt,
    render_tools,
)
from core.reflex.tool_registry import ToolInfo

NIGHT_UTC = datetime(2026, 10, 8, 3, 30, tzinfo=UTC)


def _tool(service: str, *params: str, description: str = "") -> ToolInfo:
    return ToolInfo(
        name="home." + service.replace(".", "_"),
        description=service,
        parameters={p: {"type": "str", "description": description} for p in params},
        feature_name="home",
        feature_description="",
        target_service="home-service",
        audience="reflex",
    )


TOOLS = [
    _tool("switch.turn_off", "target"),
    _tool(
        "light.turn_on",
        "target",
        "brightness_pct",
        description="Available light entities: Lamp A, Lamp B",
    ),
]


def _lamp(state: str) -> LiveEntity:
    return LiveEntity(
        "light.tv_lamp", "light", True, state, {"friendly_name": "TV Lamp", "area": "Living Room"}
    )


def _event(old: str, new: str) -> StateChangedEvent:
    return StateChangedEvent(
        source="home-service", domain="home", entity_id="light.tv_lamp", old_state=old, new_state=new
    )


def _prompt(
    event: StateChangedEvent | None = None,
    entities: dict[str, LiveEntity] | None = None,
    preferences: str = "- Prefers dim light for films",
    tools: list[ToolInfo] = TOOLS,
) -> str:
    return build_state_change_prompt(
        event=event or _event("off", "on"),
        preferences=preferences,
        tools=tools,
        entities=entities if entities is not None else {"light.tv_lamp": _lamp("on")},
        now=NIGHT_UTC,
        tz_name="UTC",
    )


def test_sections_run_from_stable_to_volatile() -> None:
    prompt = _prompt()
    marks = ["Tools:", "## Preferences", "## Now", "## House", "## What changed", "## Decision (JSON only):"]

    positions = [prompt.index(mark) for mark in marks]

    assert positions == sorted(positions)
    assert prompt.endswith("## Decision (JSON only):")


def test_the_cacheable_prefix_is_identical_across_events() -> None:
    first = _prompt(_event("off", "on"), {"light.tv_lamp": _lamp("on")})
    second = _prompt(_event("on", "off"), {"light.tv_lamp": _lamp("off")})

    assert first[: first.index("## Now")] == second[: second.index("## Now")]
    assert first != second


def test_tools_are_compact_sorted_and_free_of_entity_lists() -> None:
    assert render_tools(TOOLS) == (
        "Tools:\n"
        "- home.light_turn_on(target, brightness_pct) [home-service]: light.turn_on\n"
        "- home.switch_turn_off(target) [home-service]: switch.turn_off"
    )
    assert "Available light entities" not in _prompt()


def test_no_tools_says_so() -> None:
    assert render_tools([]) == "No tools available."


def test_blank_preferences_say_none_recorded() -> None:
    assert "## Preferences\nNone recorded yet." in _prompt(preferences="  \n")


def test_the_rules_spell_out_the_decision_format() -> None:
    prompt = _prompt()

    assert '{"decision": "none"}' in prompt
    assert '"decision": "act" | "ask"' in prompt
    for label in ("- act:", "- ask:", "- none:"):
        assert label in prompt


def test_a_trigger_prompt_names_the_trigger_and_says_the_owner_knows() -> None:
    prompt = build_trigger_prompt(
        event=TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time"),
        preferences="",
        tools=TOOLS,
        entities={"light.tv_lamp": _lamp("on")},
        now=NIGHT_UTC,
        tz_name="UTC",
    )

    assert "already being notified" in prompt
    assert "## Trigger fired\nbedtime (time)" in prompt
    assert "## House\nLiving Room: TV Lamp on" in prompt
    assert "## What changed" not in prompt


def _production_tools() -> list[ToolInfo]:
    """The nine Reflex tools production registered on 2026-10-07."""
    return [
        _tool("light.turn_on", "target", "brightness_pct"),
        _tool("light.turn_off", "target"),
        _tool("media_player.turn_on", "target"),
        _tool("media_player.turn_off", "target"),
        _tool("media_player.media_play", "target"),
        _tool("media_player.media_pause", "target"),
        _tool("media_player.volume_set", "target", "volume_level"),
        _tool("switch.turn_on", "target"),
        _tool("switch.turn_off", "target"),
    ]


def _production_shaped() -> dict[str, LiveEntity]:
    """79 actionable entities over five rooms and none, as measured on 2026-10-07."""
    rooms = ["Bedroom", "Entrance", "Kitchen", "Living Room", "Office", None]
    counts = {"light": 34, "media_player": 22, "switch": 21, "climate": 1, "vacuum": 1}
    entities: dict[str, LiveEntity] = {}
    n = 0
    for domain, count in counts.items():
        for i in range(count):
            room = rooms[n % len(rooms)]
            n += 1
            label = domain.replace("_", " ").title()
            attributes: dict[str, object] = {"friendly_name": f"{room or 'Spare'} {label} {i + 1}"}
            if room:
                attributes["area"] = room
            entity_id = f"{domain}.{domain}_{i + 1}"
            entities[entity_id] = LiveEntity(entity_id, domain, True, "off", attributes)
    for p in ("a", "b"):
        entities[f"person.{p}"] = LiveEntity(
            f"person.{p}", "person", True, "home", {"friendly_name": f"Person {p.upper()}"}
        )
    entities["sun.sun"] = LiveEntity("sun.sun", "sun", False, "below_horizon", {})
    return entities


def test_a_production_shaped_house_keeps_the_prompt_small() -> None:
    prompt = _prompt(entities=_production_shaped(), tools=_production_tools())

    assert prompt.count(" · ") >= 70  # every actionable entity made it in
    # At ~4 characters a token, 4,500 characters is about 1,100 tokens (was ~14,400).
    assert len(prompt) < 4_500
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/core/reflex/test_prompt_assembly.py -q -p no:randomly`
Expected: FAIL — `ImportError: cannot import name 'build_state_change_prompt'`.

- [ ] **Step 3: Implement assembly**

In `core/reflex/prompt.py`, extend the `TYPE_CHECKING` block with:

```python
    from collections.abc import Sequence

    from core.reflex.tool_registry import ToolInfo
```

(merge `Sequence` into the existing `from collections.abc import Mapping` line), add below `NO_DEVICES`:

```python
NO_TOOLS = "No tools available."
NO_PREFERENCES = "None recorded yet."

STATE_CHANGE_INTRO = (
    "You are Alfred's Reflex Engine, the quiet steward of a home. One thing in the house "
    "just changed. Decide whether to do something about it."
)
TRIGGER_INTRO = (
    "You are Alfred's Reflex Engine, the quiet steward of a home. A trigger the owner set "
    "up has just fired, and the owner is already being notified about it. Decide whether "
    "the home should also do something."
)
DECISION_RULES = """\
- act: the right move is obvious. Common sense or a stated preference makes it plainly
  what the household wants, and doing it would surprise no one at home.
- ask: a move is plausible, but you are not sure it is wanted.
- none: nothing needs doing. This is the usual answer.

Use only the tools below. Target a room by its name in the House section, or a device by
its name.

Respond with JSON only. Either {"decision": "none"} or
{"decision": "act" | "ask", "reason": "<one short sentence>", "tool_name": "...",
 "target_service": "...", "parameters": {...}}"""
```

and append at the end of the file:

```python
def render_tools(tools: Sequence[ToolInfo]) -> str:
    """One line per tool, sorted. Parameter descriptions stay out: they list every entity."""
    if not tools:
        return NO_TOOLS
    lines = ["Tools:"]
    for tool in sorted(tools, key=lambda t: t.name):
        line = f"- {tool.name}({', '.join(tool.parameters)}) [{tool.target_service}]"
        lines.append(f"{line}: {tool.description}" if tool.description else line)
    return "\n".join(lines)


def _assemble(
    intro: str,
    *,
    preferences: str,
    tools: Sequence[ToolInfo],
    entities: Mapping[str, LiveEntity] | None,
    now: datetime,
    tz_name: str,
    change_heading: str,
    change: str,
) -> str:
    # Stable → volatile. Keep this order: it is what lets vLLM reuse the prefix.
    return "\n\n".join(
        [
            f"{intro}\n\n{DECISION_RULES}\n\n{render_tools(tools)}",
            f"## Preferences\n{preferences.strip() or NO_PREFERENCES}",
            f"## Now\n{render_now(now, tz_name, entities)}",
            f"## House\n{render_house(entities)}",
            f"## {change_heading}\n{change}",
            "## Decision (JSON only):",
        ]
    )


def build_state_change_prompt(
    *,
    event: StateChangedEvent,
    preferences: str,
    tools: Sequence[ToolInfo],
    entities: Mapping[str, LiveEntity] | None,
    now: datetime,
    tz_name: str,
) -> str:
    """The Reflex prompt for one state change."""
    return _assemble(
        STATE_CHANGE_INTRO,
        preferences=preferences,
        tools=tools,
        entities=entities,
        now=now,
        tz_name=tz_name,
        change_heading="What changed",
        change=render_event(event, entities),
    )


def build_trigger_prompt(
    *,
    event: TriggerFired,
    preferences: str,
    tools: Sequence[ToolInfo],
    entities: Mapping[str, LiveEntity] | None,
    now: datetime,
    tz_name: str,
) -> str:
    """The Reflex prompt for a trigger that fired; the owner is already being notified."""
    return _assemble(
        TRIGGER_INTRO,
        preferences=preferences,
        tools=tools,
        entities=entities,
        now=now,
        tz_name=tz_name,
        change_heading="Trigger fired",
        change=render_trigger(event),
    )
```

- [ ] **Step 4: Run the tests to make sure they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/core/reflex/test_prompt_sections.py tests/core/reflex/test_prompt_assembly.py -q -p no:randomly && uv run mypy --strict core/reflex/prompt.py`
Expected: all passed; mypy `Success`. If the size test fails, print `len(prompt)` and the House section and report it — do not raise the bound without asking.

- [ ] **Step 5: Commit**

```bash
git add core/reflex/prompt.py tests/core/reflex/test_prompt_assembly.py
git commit -m "feat(reflex): assemble the prompt stable to volatile, about 1k tokens (#285)"
```

---

### Task 6: The engine returns proposals

**Files:**
- Modify: `core/reflex/context_reader.py` (add `get_snapshot`, `get_user_timezone`)
- Rewrite: `core/reflex/engine.py` (full new content below)
- Test: `tests/core/reflex/test_context_reader_live.py` (create)
- Test: `tests/core/reflex/test_engine_decisions.py` (create)
- Modify: `core/reflex/tests/test_engine.py` (delete the old-API tests)
- Delete: `tests/core/reflex/test_engine_public_api.py`
- Modify: `tests/integration/test_reflex_end_to_end.py`
- Modify: `evals/pipeline.py`, `tests/evals/test_pipeline.py` — **skip both if `evals/pipeline.py` no longer exists** (PRD eval slice 1 deletes the old harness)
- Docs: `docs/architecture.md` §3.2, `core/CLAUDE.md` Reflex section, `.claude/rules/core/reflex-engine.md`

**Interfaces:**
- Consumes: `parse_decision` (Task 2); `build_state_change_prompt`, `build_trigger_prompt`, `index_snapshot`, `LiveEntity` (Tasks 4–5); `ReflexProposal` (Task 1).
- Produces:
  - `ContextReader.get_snapshot() -> ContextSnapshot | None` and `ContextReader.get_user_timezone() -> str`.
  - `ReflexEngine(preferences_dir, tool_registry, context_reader=None, memory_reader=None, *, clock: Callable[[], datetime] = _utc_now)`.
  - `ReflexEngine.process_event(event: StateChangedEvent) -> ReflexProposal` and `ReflexEngine.process_trigger_fired(event: TriggerFired) -> ReflexProposal`. Both still raise when the model call fails.
  - Removed: `build_prompt`, `parse_response`, `parse_trigger_response`, `_parse_slm_json`, `_build_system_prompt`, `_get_tools_and_prompt`, `_SYSTEM_PROMPT_TEMPLATE`, `_TRIGGER_FIRED_PROMPT_TEMPLATE`, `_build_tool_section`. Kept: `build_notification_body`, `reload_tools`, `_cached_preferences`.

- [ ] **Step 1: Write the failing ContextReader tests**

Create `tests/core/reflex/test_context_reader_live.py`:

```python
"""ContextReader feeds Reflex the raw snapshot and the user's timezone (#285)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

from core.reflex.context_reader import ContextReader
from sdk.alfred_sdk.context import ContextEntry, ContextSnapshot

if TYPE_CHECKING:
    import pytest


async def test_get_snapshot_reads_live_state_fresh_every_time() -> None:
    snapshot = ContextSnapshot(
        controllable={"light": [ContextEntry(entity_id="light.a", state="on")]}
    )
    with patch(
        "core.reflex.context_reader.read_live_state", new=AsyncMock(return_value=snapshot)
    ) as read:
        reader = ContextReader(redis=AsyncMock())
        assert await reader.get_snapshot() is snapshot
        assert await reader.get_snapshot() is snapshot

    assert read.await_count == 2  # no cache: #283 removed it on purpose


async def test_get_snapshot_is_none_without_live_state() -> None:
    with patch("core.reflex.context_reader.read_live_state", new=AsyncMock(return_value=None)):
        assert await ContextReader(redis=AsyncMock()).get_snapshot() is None


async def test_get_user_timezone_reads_the_stored_zone() -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=b"America/Chicago")

    assert await ContextReader(redis=redis).get_user_timezone() == "America/Chicago"


async def test_get_user_timezone_falls_back_to_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALFRED_TIMEZONE", raising=False)
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)

    assert await ContextReader(redis=redis).get_user_timezone() == "UTC"
```

- [ ] **Step 2: Write the failing engine tests**

Create `tests/core/reflex/test_engine_decisions.py`:

```python
"""ReflexEngine — one event in, a ReflexProposal out (#285)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from bus.schemas.events import StateChangedEvent, TriggerFired
from core.reflex.context_reader import LIVE_STATE_UNAVAILABLE
from core.reflex.engine import ReflexEngine
from core.reflex.tool_registry import ToolInfo
from sdk.alfred_sdk.context import ContextEntry, ContextSnapshot

# 03:30 UTC on Thu 8 Oct 2026 is 22:30 on Wed 7 Oct in Chicago (CDT, UTC-5).
NIGHT_UTC = datetime(2026, 10, 8, 3, 30, tzinfo=UTC)

TOOLS = [
    ToolInfo(
        name="home.light_turn_on",
        description="light.turn_on",
        parameters={
            "target": {"type": "str", "description": "Available light entities: Lamp A, Lamp B"},
            "brightness_pct": {"type": "float"},
        },
        feature_name="home",
        feature_description="",
        target_service="home-service",
        audience="reflex",
    ),
    ToolInfo(
        name="home.lock_unlock",
        description="lock.unlock",
        parameters={"target": {"type": "str"}},
        feature_name="home",
        feature_description="",
        target_service="home-service",
        audience="conscious",
    ),
]

TV_PLAYS = StateChangedEvent(
    source="home-service",
    domain="home",
    entity_id="media_player.living_room_tv",
    old_state="paused",
    new_state="playing",
    attributes={"friendly_name": "Living Room TV", "media_title": "A Film"},
)

ACT = {
    "decision": "act",
    "reason": "Film at night",
    "tool_name": "home.light_turn_on",
    "target_service": "home-service",
    "parameters": {"target": "Living Room", "brightness_pct": 30},
}


def _snapshot() -> ContextSnapshot:
    return ContextSnapshot(
        controllable={
            "light": [
                ContextEntry(
                    entity_id="light.tv_lamp",
                    state="on",
                    attributes={"friendly_name": "TV Lamp", "area": "Living Room", "brightness": 255},
                )
            ],
            "media_player": [
                ContextEntry(
                    entity_id="media_player.living_room_tv",
                    state="playing",
                    attributes={"friendly_name": "Living Room TV", "area": "Living Room"},
                )
            ],
            "person": [
                ContextEntry(entity_id="person.a", state="home", attributes={"friendly_name": "Person A"})
            ],
        },
        sensors={"sun": [ContextEntry(entity_id="sun.sun", state="below_horizon")]},
    )


def _engine(
    snapshot: ContextSnapshot | None = None, *, live: bool = True
) -> tuple[ReflexEngine, AsyncMock]:
    registry = AsyncMock()
    registry.get_tools = AsyncMock(return_value=TOOLS)
    reader = None
    if live:
        reader = AsyncMock()
        reader.get_snapshot = AsyncMock(return_value=snapshot)
        reader.get_user_timezone = AsyncMock(return_value="America/Chicago")
    engine = ReflexEngine(
        preferences_dir="/unused",
        tool_registry=registry,
        context_reader=reader,
        clock=lambda: NIGHT_UTC,
    )
    engine._cached_preferences = "- Prefers dim light for films"
    return engine, registry


def _reply(payload: dict[str, object] | str) -> AsyncMock:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return AsyncMock(return_value={"response": text})


async def test_an_act_reply_becomes_a_proposal_with_its_action() -> None:
    engine, _ = _engine(_snapshot())

    with patch("core.reflex.inference.infer", new=_reply(ACT)):
        proposal = await engine.process_event(TV_PLAYS)

    assert proposal.decision == "act"
    assert proposal.action is not None
    assert proposal.action.tool_name == "home.light_turn_on"
    assert proposal.action.reason == "Film at night"


async def test_the_prompt_shows_now_the_house_and_the_change() -> None:
    engine, _ = _engine(_snapshot())
    infer = _reply({"decision": "none"})

    with patch("core.reflex.inference.infer", new=infer):
        await engine.process_event(TV_PLAYS)

    prompt = infer.await_args.args[0]
    assert "## Preferences\n- Prefers dim light for films" in prompt
    assert "## Now\nWed 7 Oct, 22:30 (night) · sun down\nPeople: Person A home" in prompt
    assert "## House\nLiving Room: Living Room TV playing · TV Lamp on 100%" in prompt
    assert '## What changed\nLiving Room TV (Living Room): paused → playing · "A Film"' in prompt


async def test_only_reflex_tools_reach_the_prompt_without_their_entity_lists() -> None:
    engine, _ = _engine(_snapshot())
    infer = _reply({"decision": "none"})

    with patch("core.reflex.inference.infer", new=infer):
        await engine.process_event(TV_PLAYS)

    prompt = infer.await_args.args[0]
    assert "- home.light_turn_on(target, brightness_pct) [home-service]: light.turn_on" in prompt
    assert "home.lock_unlock" not in prompt
    assert "Available light entities" not in prompt


async def test_a_proposal_naming_a_conscious_tool_is_invalid() -> None:
    engine, _ = _engine(_snapshot())

    with patch("core.reflex.inference.infer", new=_reply({**ACT, "tool_name": "home.lock_unlock"})):
        proposal = await engine.process_event(TV_PLAYS)

    assert proposal.decision == "invalid"


async def test_no_live_state_still_decides() -> None:
    engine, _ = _engine(None)
    infer = _reply({"decision": "none"})

    with patch("core.reflex.inference.infer", new=infer):
        proposal = await engine.process_event(TV_PLAYS)

    prompt = infer.await_args.args[0]
    assert f"## Now\nWed 7 Oct, 22:30 (night)\n\n## House\n{LIVE_STATE_UNAVAILABLE}" in prompt
    assert proposal.decision == "none"


async def test_without_a_context_reader_the_clock_is_utc() -> None:
    engine, _ = _engine(live=False)
    infer = _reply({"decision": "none"})

    with patch("core.reflex.inference.infer", new=infer):
        await engine.process_event(TV_PLAYS)

    assert "## Now\nThu 8 Oct, 03:30 (night)" in infer.await_args.args[0]


async def test_tools_are_cached_between_events_until_reloaded() -> None:
    engine, registry = _engine(_snapshot())

    with patch("core.reflex.inference.infer", new=_reply({"decision": "none"})):
        await engine.process_event(TV_PLAYS)
        await engine.process_event(TV_PLAYS)
        assert registry.get_tools.await_count == 1
        await engine.reload_tools()
        await engine.process_event(TV_PLAYS)

    assert registry.get_tools.await_count == 2


async def test_a_model_failure_propagates() -> None:
    engine, _ = _engine(_snapshot())

    with (
        patch("core.reflex.inference.infer", new=AsyncMock(side_effect=ConnectionError("down"))),
        pytest.raises(ConnectionError),
    ):
        await engine.process_event(TV_PLAYS)


async def test_an_unparseable_reply_is_an_invalid_proposal() -> None:
    engine, _ = _engine(_snapshot())

    with patch("core.reflex.inference.infer", new=_reply("Sure! Dimming.")):
        proposal = await engine.process_event(TV_PLAYS)

    assert proposal.decision == "invalid"
    assert proposal.raw == "Sure! Dimming."


async def test_a_trigger_gets_now_and_the_house_and_names_itself() -> None:
    engine, _ = _engine(_snapshot())
    infer = _reply({"decision": "none"})
    trigger = TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time")

    with patch("core.reflex.inference.infer", new=infer):
        proposal = await engine.process_trigger_fired(trigger)

    prompt = infer.await_args.args[0]
    assert "already being notified" in prompt
    assert "## Trigger fired\nbedtime (time)" in prompt
    assert "## House\nLiving Room:" in prompt
    assert "## What changed" not in prompt
    assert proposal.decision == "none"
```

- [ ] **Step 3: Run them to make sure they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/core/reflex/test_context_reader_live.py tests/core/reflex/test_engine_decisions.py -q -p no:randomly`
Expected: FAIL — `AttributeError: ... has no attribute 'get_snapshot'` and `TypeError: ... unexpected keyword argument 'clock'`.

- [ ] **Step 4: Extend ContextReader**

In `core/reflex/context_reader.py` add the import `from shared import usertime` (runtime, next to the `read_live_state` import) and these methods to `ContextReader`, after `get_rendered_context`:

```python
    async def get_snapshot(self) -> ContextSnapshot | None:
        """Every service's live state merged, read fresh; None when no service has any."""
        return await read_live_state(self._redis)

    async def get_user_timezone(self) -> str:
        """The user's IANA timezone: stored, then ``ALFRED_TIMEZONE``, then UTC."""
        return await usertime.get_user_timezone(self._redis)
```

- [ ] **Step 5: Rewrite the engine**

Replace the whole of `core/reflex/engine.py` with:

```python
"""Reflex Engine — System 1 fast-path SLM inference.

One event in, a ReflexProposal out (#285): act, ask, none or invalid. Reads
preferences, Reflex's tools and live state, builds the prompt with
``core.reflex.prompt`` and parses the reply with ``core.reflex.decision``.

Design for eval-ability: structured (event, preferences, live state) in → structured
proposal out. No side effects — in shadow mode nothing a proposal names is executed.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.memory.reader import MemoryReader
from core.reflex import inference
from core.reflex.decision import parse_decision
from core.reflex.prompt import build_state_change_prompt, build_trigger_prompt, index_snapshot
from sdk.alfred_sdk.telemetry import track_latency
from shared.traced import traced

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from bus.schemas.events import ReflexProposal, StateChangedEvent, TriggerFired
    from core.reflex.context_reader import ContextReader
    from core.reflex.prompt import LiveEntity
    from core.reflex.tool_registry import ToolInfo, ToolRegistry

logger = logging.getLogger(__name__)


def build_notification_body(event: TriggerFired) -> str:
    """Build a human-readable notification body from TriggerFired context."""
    parts: list[str] = []
    if event.context.get("event_entity"):
        entity = event.context["event_entity"]
        state = event.context.get("event_state")
        parts.append(f"{entity}: {state}" if state else str(entity))
    if event.context.get("evaluated_at"):
        parts.append(f"Fired at {event.context['evaluated_at']}")
    return " | ".join(parts) if parts else f"Trigger '{event.trigger_name}' fired"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ReflexEngine:
    """The System 1 fast-path inference engine."""

    TOOL_CACHE_TTL = 300.0  # Re-read tool registry from Redis every 5 minutes

    def __init__(
        self,
        preferences_dir: str,
        tool_registry: ToolRegistry,
        context_reader: ContextReader | None = None,
        memory_reader: MemoryReader | None = None,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.preferences_dir = preferences_dir
        self._registry = tool_registry
        self._context_reader = context_reader
        self._memory_reader = memory_reader
        self._clock = clock
        self._cached_preferences: str | None = None
        self._cached_tools: list[ToolInfo] | None = None
        self._cache_time: float = 0.0

    def _get_preferences(self) -> str:
        """Return cached preferences, loading from disk on first call."""
        if self._cached_preferences is None:
            if self._memory_reader is not None:
                self._cached_preferences = self._memory_reader.get_preferences()
            else:
                from pathlib import Path

                reader = MemoryReader(
                    preferences_dir=Path(self.preferences_dir),
                    profile_dir=Path(self.preferences_dir).parent / "profile",
                )
                self._cached_preferences = reader.get_preferences()
        return self._cached_preferences

    async def _get_tools(self) -> list[ToolInfo]:
        """Reflex-audience tools, TTL-cached.

        Only tools tagged ``audience == "reflex"`` reach the prompt — the first layer
        of tiered autonomy (contract C9). Untagged tools default to "conscious".
        """
        now = time.monotonic()
        if self._cached_tools is None or (now - self._cache_time) > self.TOOL_CACHE_TTL:
            all_tools = await self._registry.get_tools()
            self._cached_tools = [t for t in all_tools if t.audience == "reflex"]
            self._cache_time = now
        return self._cached_tools

    async def reload_tools(self) -> None:
        """Invalidate cached tools, forcing re-fetch on next event."""
        self._cached_tools = None

    async def _live_context(self) -> tuple[Mapping[str, LiveEntity] | None, str]:
        """Live state, indexed, and the user's timezone. Offline (no reader): none, UTC."""
        if self._context_reader is None:
            return None, "UTC"
        snapshot = await self._context_reader.get_snapshot()
        tz_name = await self._context_reader.get_user_timezone()
        return (None if snapshot is None else index_snapshot(snapshot)), tz_name

    @traced(name="reflex.process_event")
    @track_latency(category="reflex")
    async def process_event(self, event: StateChangedEvent) -> ReflexProposal:
        """Decide about one state change. Raises if the model call fails."""
        tools = await self._get_tools()
        entities, tz_name = await self._live_context()
        prompt = build_state_change_prompt(
            event=event,
            preferences=self._get_preferences(),
            tools=tools,
            entities=entities,
            now=self._clock(),
            tz_name=tz_name,
        )
        response = await inference.infer(prompt)
        return parse_decision(str(response.get("response", "")), tools)

    @traced(name="reflex.process_trigger_fired")
    @track_latency(category="reflex")
    async def process_trigger_fired(self, event: TriggerFired) -> ReflexProposal:
        """Decide whether a fired trigger calls for more than its notification."""
        tools = await self._get_tools()
        entities, tz_name = await self._live_context()
        prompt = build_trigger_prompt(
            event=event,
            preferences=self._get_preferences(),
            tools=tools,
            entities=entities,
            now=self._clock(),
            tz_name=tz_name,
        )
        response = await inference.infer(prompt)
        return parse_decision(str(response.get("response", "")), tools)
```

- [ ] **Step 6: Run the new tests to make sure they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/core/reflex/test_context_reader_live.py tests/core/reflex/test_engine_decisions.py tests/core/reflex/test_audience_filter.py -q -p no:randomly`
Expected: all passed. `test_audience_filter.py` passes unchanged: its fake reply `{"action": "none"}` still parses as none, and it only checks the prompt's tools.

- [ ] **Step 7: Retire the tests of the removed API**

1. `core/reflex/tests/test_engine.py`: delete from `def _make_tools() -> list[ToolInfo]:` (line 19) down to, but not including, the `# --- build_notification_body tests ---` comment (line 393). Keep the four `testbuild_notification_body_*` tests. Then run `uv run ruff check --fix core/reflex/tests/test_engine.py`, and if an empty `if TYPE_CHECKING:` block remains, delete it and its `TYPE_CHECKING` import.
2. `git rm tests/core/reflex/test_engine_public_api.py` — `build_prompt` and `parse_response` are gone; their coverage lives in `test_prompt_*.py` and `test_decision.py`.
3. `tests/integration/test_reflex_end_to_end.py`: in `test_full_reflex_pipeline`, change the reply and the assertions to:

```python
    ollama_response = {
        "response": json.dumps(
            {
                "decision": "act",
                "reason": "TV on, dim for viewing",
                "tool_name": "lighting.dim_lights",
                "target_service": "home-service",
                "parameters": {"room": "living_room", "level": 20},
            }
        ),
        "prompt_tokens": 200,
        "completion_tokens": 25,
        "total_tokens": 225,
    }
```

```python
        proposal = await engine.process_event(tv_on_event)

    # Structured output verification (eval contract)
    assert proposal.decision == "act"
    assert proposal.reason == "TV on, dim for viewing"
    action = proposal.action
    assert isinstance(action, ActionRequest)
    assert action.tool_name == "lighting.dim_lights"
    assert action.target_service == "home-service"
    assert action.parameters["room"] == "living_room"
    assert 0 <= action.parameters["level"] <= 100
```

and in `test_reflex_no_action_for_irrelevant_event` replace `action = await engine.process_event(temp_event)` / `assert action is None` with:

```python
        proposal = await engine.process_event(temp_event)

    assert proposal.decision == "none"
    assert proposal.action is None
```

- [ ] **Step 8: Move the old eval harness onto the new API (skip if `evals/pipeline.py` is gone)**

In `evals/pipeline.py`:
- Replace the imports `from core.reflex.engine import ReflexEngine` and `from evals.context_fixtures import load_context_text` with `from core.reflex.decision import parse_decision` and `from core.reflex.prompt import build_state_change_prompt`. Drop `cast` from the `typing` import and `AioRedis` from the `TYPE_CHECKING` block if nothing else uses them; `ToolRegistry` stays (`get_registered_services`). The harness keeps passing every tool it was given, as before, so `parse_decision` validates against `ctx.tools`.
- In `EvalContext.__init__`, delete the `self.engine = ReflexEngine(...)` block and its comment.
- In `run_scenario`, delete the two "Per-scenario context fixture" lines and replace the prompt and parse lines with:

```python
    # Context fixtures predate live state (#283) and the House section (#285). This
    # harness is being replaced by the PRD eval suite, so it renders no live state.
    resolved_model = model or ctx.model
    prompt = build_state_change_prompt(
        event=scenario.event,
        preferences=preferences_text,
        tools=ctx.tools,
        entities=None,
        now=datetime.now(UTC),
        tz_name="UTC",
    )
```

```python
    # Parse using the engine's real logic
    parsed_action = parse_decision(str(response.get("response", "")), ctx.tools).action
```

In `tests/evals/test_pipeline.py`:
- replace `assert "media_player.tv" in trace.prompt` with `assert "TV: off → on" in trace.prompt`;
- in `test_run_scenario_with_action`, add `"decision": "act",` and `"reason": "Film time",` to the JSON reply.

Run: `grep -rn "EvalContext\|\.engine\b" evals/ | grep -v "^evals/pipeline.py"` — if anything else reads `ctx.engine`, stop and report it.

- [ ] **Step 9: Run every engine consumer**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run pytest core/reflex tests/core/reflex tests/integration/test_reflex_end_to_end.py tests/evals/test_pipeline.py -q -p no:randomly`
Expected: everything passes **except** runner/trigger tests that still mock `process_event` / `process_trigger_fired` as returning `None` or an `ActionRequest` — those are Task 7's. List them; they should be exactly in `core/reflex/tests/test_runner.py`, `core/reflex/tests/test_trigger_fired_consumer.py`, `tests/core/reflex/test_passive_observation.py`, `tests/core/reflex/test_runner_attention.py`, `tests/core/reflex/test_availability.py` and `tests/core/reflex/test_trigger_fired_reclaim.py`. Anything outside that list is a bug in this task.

Run: `grep -rn "_get_tools_and_prompt\|_cached_system_prompt\|_build_tool_section\|parse_response\|build_prompt(" --include='*.py' . ` — expected: no matches.

- [ ] **Step 10: Update the docs**

`docs/architecture.md` — replace §3.2 from `### 3.2 Reflex Engine (System 1 SLM Inference)` through the `**Ollama client** …` paragraph (just above `#### 3.2.1 AttentionSet`) with:

```markdown
### 3.2 Reflex Engine (System 1 SLM Inference)

**Files:** `core/reflex/engine.py`, `core/reflex/prompt.py`, `core/reflex/decision.py`, `core/reflex/inference.py` (backends `openai_client.py`, `ollama_client.py`)

The `ReflexEngine` class is the System 1 fast path. It is a pure inference component with no side effects -- it takes a `StateChangedEvent` (or a `TriggerFired`) and returns a `ReflexProposal`: **act**, **ask**, **none** or **invalid** ([#285](https://github.com/anirudhlath/alfred/issues/285), [design](superpowers/specs/2026-10-07-reflex-context-design.md)).

**How it works:**

1. Loads user preferences from `core/memory/preferences/` (cached after first read).
2. Fetches the `audience == "reflex"` tools from `ToolRegistry` (5-minute cache, invalidatable via `reload_tools()`).
3. Reads live state fresh through `ContextReader.get_snapshot()`, and the user's timezone through `ContextReader.get_user_timezone()`.
4. Builds the prompt with `core/reflex/prompt.py`, ordered from stable to volatile so vLLM's prefix cache covers as much as it can: rules and compact tools, `## Preferences`, `## Now` (local time, time of day, sun, people), `## House` (the actionable domains, one line per room by `attributes.area` — see [live-state.md](live-state.md#well-known-attributes)), then `## What changed` in one line or `## Trigger fired`. About 1,000 tokens.
5. Sends it through `core/reflex/inference.py` (`REFLEX_BACKEND`: `openai` for vLLM in production, `ollama` by default) with `temperature=0`, JSON output and, on the OpenAI-compatible backend, `max_tokens=150`.
6. `parse_decision()` turns the reply into a `ReflexProposal`, validating the tool against Reflex's own tool list. Anything else becomes **invalid**, keeping the raw text.

**Shadow mode (slice 1 of #285 → #286 → #287):** nothing a proposal names is executed. The runner counts every decision and records act, ask and invalid proposals; see section 2.

The `@track_latency(category="reflex")` decorator on `process_event` records inference latency to the telemetry buffer, and `@track_tokens` on each backend's `infer` records token usage.
```

`core/CLAUDE.md`, in `## Reflex (\`reflex/\`) — System 1 SLM Engine`:
- replace `Fast event → action loop via local SLM (Ollama).` with `Fast event → decision loop via a local SLM (vLLM through \`REFLEX_BACKEND=openai\` in production). Shadow mode (#285): decisions are recorded, nothing executes.`
- replace the bullet `` - `engine.py` — SLM inference with dynamic tool prompt + TriggerFired reasoning `` with `` - `engine.py` — `ReflexEngine`: gathers preferences, reflex-audience tools, live state and timezone; returns a `ReflexProposal` (act/ask/none/invalid) for StateChanged and TriggerFired ``
- add after it:
  - `` - `prompt.py` — pure prompt builder: rules + tools, Preferences, Now, House by `attributes.area`, What changed / Trigger fired; `HOUSE_DOMAINS` ``
  - `` - `decision.py` — `parse_decision()`: model reply → `ReflexProposal`, tool validated against Reflex's tools; anything else is `invalid` with the raw text ``
- append to the `context_reader.py` bullet: `` ; `get_snapshot()` and `get_user_timezone()` feed Reflex's prompt ``

`.claude/rules/core/reflex-engine.md` — replace the whole file with:

```markdown
---
paths:
  - "core/reflex/**"
---

# Reflex Engine Rules

The Reflex Engine (System 1) is the fast-path SLM that decides about events.

- MUST be eval-able: structured (event, preferences, live state) in → `ReflexProposal` out
- No side effects during inference. In shadow mode (#285) nothing a proposal names is executed; #286 adds execution
- Reads preferences from core/memory/preferences/ (read-only)
- Reads tools from ToolRegistry (Redis `alfred:tool_registry`) — NEVER hardcode tool names; only `audience == "reflex"` tools reach the prompt
- The prompt is built in `core/reflex/prompt.py`, ordered stable → volatile (rules + tools, Preferences, Now, House, What changed) so vLLM's prefix cache holds. Keep it near 1,000 tokens — never dump all of live state into it
- Reads only the well-known live-state attributes (`friendly_name`, `area`, plus per-domain details) — see docs/live-state.md
- `parse_decision()` validates the tool against Reflex's own tool list; anything else becomes an `invalid` proposal carrying the raw text, never a silent drop
- Records what it saw and decided on `alfred:reflex:observations` (proposals for act/ask/invalid, debounced passive observations for none) and counts every decision in `alfred:reflex:decisions:<UTC date>`. The Memory Ingestor writes episodic memory and ignores proposals. Reflex never writes the scratchpad
- On the state-change path, `unavailable`/`unknown` (and a missing old state) never reach the SLM — `core/reflex/availability.py` drops or bridges them before the attention gate. TriggerFired events are not bridged: triggers read the raw stream
- Target latency: sub-500ms event → decision
- All inference calls MUST use @track_latency and @track_tokens decorators
- Never call the cloud LLM (System 2) from the reflex path
- Backend via `REFLEX_BACKEND` (`openai` = vLLM in production, `ollama` default); model via `OPENAI_COMPAT_MODEL` / `OLLAMA_MODEL`
- Starts with no tools — discovers them dynamically via TTL-based cache refresh (5 min)
```

- [ ] **Step 11: Type-check and commit**

Run: `uv run mypy --strict core/reflex/ evals/ bus/`
Expected: `Success`.

```bash
git add -A core/reflex tests/core/reflex tests/integration/test_reflex_end_to_end.py evals/pipeline.py tests/evals/test_pipeline.py docs/architecture.md core/CLAUDE.md .claude/rules/core/reflex-engine.md
git commit -m "feat(reflex): the engine returns proposals built from the new prompt (#285)"
```

(Runner and trigger tests are still red; Task 7 turns them green. Note this in the commit body if your reviewer runs the full suite per commit.)

---

### Task 7: Shadow recording — count every decision, publish act/ask/invalid, execute nothing

**Files:**
- Modify: `shared/streams.py` (add `REFLEX_DECISIONS_PREFIX`)
- Modify: `core/reflex/runner.py`
- Modify: `core/reflex/__main__.py` (`_handle_trigger_fired` Path B and imports)
- Modify tests: `core/reflex/tests/test_runner.py`, `core/reflex/tests/test_trigger_fired_consumer.py`, `tests/core/reflex/test_passive_observation.py`, `tests/core/reflex/test_runner_attention.py`, `tests/core/reflex/test_availability.py`, `tests/core/reflex/test_trigger_fired_reclaim.py`
- Docs: `docs/architecture.md` §2, `core/CLAUDE.md` runner bullet

**Interfaces:**
- Consumes: `ReflexEngine.process_event` / `process_trigger_fired -> ReflexProposal` (Task 6); `ReflexProposal` (Task 1).
- Produces (in `core.reflex.runner`):
  - `DECISION_COUNT_TTL_SECONDS = 30 * 24 * 3600`
  - `decisions_key(day: date) -> str` → `"alfred:reflex:decisions:YYYY-MM-DD"`
  - `count_decision(redis: AioRedis, decision: str, *, now: datetime | None = None) -> None` — best-effort, never raises
  - `publish_proposal(redis: AioRedis, stream: str, origin: Literal["state_change", "trigger_fired"], trigger_event: BaseModel, proposal: ReflexProposal) -> None` — raises on Redis failure; callers isolate it
  - `process_stream_entry(...)` — same signature; always returns `False` in shadow mode
  - Kept: `publish_observation` — `core/routing/domain_router.py` records tier rejections with it
- `shared.streams.REFLEX_DECISIONS_PREFIX = "alfred:reflex:decisions:"`

- [ ] **Step 1: Write the failing runner tests**

In `core/reflex/tests/test_runner.py`:
- change the import line to `from bus.schemas.events import ActionRequest, ReflexProposal, StateChangedEvent`, add `from datetime import UTC, datetime` above it and `from shared.streams import OBSERVED_ENTITY_PREFIX` below it (drop `ActionResult` if ruff reports it unused);
- delete `test_process_stream_entry_produces_action` and `test_process_stream_entry_publishes_reflex_observation` (lines 12–117, decorators included) and put this in their place:

```python
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
```

- and in the two remaining tests that set `mock_engine.process_event.return_value = None`, change that line to `mock_engine.process_event.return_value = ReflexProposal(decision="none")`.

- [ ] **Step 2: Run them to make sure they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run pytest core/reflex/tests/test_runner.py -q -p no:randomly`
Expected: FAIL — `ImportError: cannot import name 'DECISION_COUNT_TTL_SECONDS'` and the act test failing on `execute_action` being awaited.

- [ ] **Step 3: Add the stream constant**

In `shared/streams.py`, after `REFLEX_OBSERVATIONS_STREAM = "alfred:reflex:observations"`:

```python
# Daily Reflex decision counts (#285): one hash per UTC day, one field per decision.
REFLEX_DECISIONS_PREFIX = "alfred:reflex:decisions:"
```

- [ ] **Step 4: Implement shadow recording in the runner**

In `core/reflex/runner.py`:

1. Update the module docstring's second paragraph to: `Reads events from Redis Streams (consumer group), runs the Reflex Engine, and records its decisions. Shadow mode (#285): nothing executes — act, ask and invalid proposals are published as observations, and every decision is counted.`
2. Imports: add `from datetime import UTC, datetime` (runtime) and `from datetime import date` under `TYPE_CHECKING`; change `from shared.streams import OBSERVED_ENTITY_PREFIX, decode_stream_value` to also import `REFLEX_DECISIONS_PREFIX`; under `TYPE_CHECKING` change `from bus.schemas.events import ActionRequest` to `from bus.schemas.events import ActionRequest, ReflexProposal`.
3. Keep `publish_observation` (`core/routing/domain_router.py` records tier rejections with it) and add directly below it:

```python
DECISION_COUNT_TTL_SECONDS = 30 * 24 * 3600


def decisions_key(day: date) -> str:
    """The hash that counts one UTC day's Reflex decisions."""
    return f"{REFLEX_DECISIONS_PREFIX}{day.isoformat()}"


async def count_decision(redis: AioRedis, decision: str, *, now: datetime | None = None) -> None:
    """Count one decision for its UTC day. Best-effort: failures are logged, never raised.

    "none" observations are debounced per entity, so the stream undercounts them; these
    counters are the shadow report's true totals.
    """
    key = decisions_key((now or datetime.now(UTC)).astimezone(UTC).date())
    try:
        await redis.hincrby(key, decision, 1)
        await redis.expire(key, DECISION_COUNT_TTL_SECONDS)
    except Exception as e:
        logger.warning("Decision count failed (%s): %s", decision, e)


async def publish_proposal(
    redis: AioRedis,
    stream: str,
    origin: Literal["state_change", "trigger_fired"],
    trigger_event: BaseModel,
    proposal: ReflexProposal,
) -> None:
    """Publish an observation carrying a proposal Reflex did not execute."""
    observation = ReflexObservation(
        source="reflex-engine",
        origin=origin,
        trigger_event=trigger_event.model_dump(),
        proposal=proposal,
    )
    await redis.xadd(stream, {"event": observation.model_dump_json()})
```

If mypy flags the `hincrby` / `expire` awaits (`Awaitable[int] | int`), append `# type: ignore[misc,unused-ignore]` to those two lines, as `core/conscious/session.py` does for `expire`.

4. In `process_stream_entry`, replace the docstring's first two sentences with `Process a single Redis Stream entry. Returns True if an action was taken — never, in shadow mode (#285); ``agent`` and ``result_stream`` wait for #286.` and keep the rest. Then replace everything from the `# NOTE: engine.process_event() calls Ollama.` comment to the end of the function with:

```python
    # engine.process_event() calls the model. If it is down this raises
    # (httpx.ConnectError, etc.) and the caller does NOT ACK — Redis redelivers.
    proposal = await engine.process_event(event)
    await count_decision(redis, proposal.decision)

    if proposal.decision == "none":
        # Record it rather than dropping it. Without this Alfred remembers
        # only what it did, never what it saw, and pattern detection has
        # nothing to run over.
        #
        # Isolated — failures don't block ACK. Recording is bookkeeping for an
        # event the engine has already finished handling, and under Redis
        # maxmemory the deny-oom commands it needs (SET, XADD) are rejected
        # while XREADGROUP/XACK still succeed. Propagating would leave every
        # no-action event un-ACKed and feed each one back into a fresh SLM
        # inference on the next reclaim pass.
        try:
            await observe_passively(redis, observation_stream, event)
        except Exception as e:
            logger.warning("Passive observation failed for %s: %s", event.entity_id, e)
        return False

    # Shadow mode (#285): record what Reflex would do and execute nothing. Not
    # debounced — act/ask/invalid are rare and are the evidence this slice collects.
    # Isolated like the passive path, for the same maxmemory reason.
    if proposal.decision == "invalid":
        logger.warning("Invalid Reflex output for %s: %s", event.entity_id, proposal.problem)
    try:
        await publish_proposal(redis, observation_stream, "state_change", event, proposal)
    except Exception as e:
        logger.warning("Proposal observation failed for %s: %s", event.entity_id, e)
        return False
    tool = proposal.action.tool_name if proposal.action is not None else "-"
    logger.info("Shadow %s for %s: %s", proposal.decision, event.entity_id, tool)
    return False
```

- [ ] **Step 5: Shadow the TriggerFired path**

In `core/reflex/__main__.py`:
- change `from core.reflex.runner import ensure_consumer_group, process_stream_entry, publish_observation` to `from core.reflex.runner import count_decision, ensure_consumer_group, process_stream_entry, publish_proposal`;
- in `_handle_trigger_fired`, append to the docstring: `In shadow mode (#285) ``agent`` is unused: Reflex's decision is recorded, never executed.` and replace the whole `# Path B` block with:

```python
    # Path B: Reflex's decision, recorded in shadow (#285) — nothing executes.
    # Isolated: failures don't block ACK.
    try:
        proposal = await engine.process_trigger_fired(trigger_event)
        await count_decision(redis, proposal.decision)
        if proposal.decision != "none":
            await publish_proposal(
                redis, REFLEX_OBSERVATIONS_STREAM, "trigger_fired", trigger_event, proposal
            )
    except Exception as e:
        logger.error("SLM reasoning failed for trigger '%s': %s", trigger_event.trigger_name, e)
```

Leave `HOME_ACTION_RESULTS_STREAM` imported: `RESULT_STREAM` still uses it.

- [ ] **Step 6: Run the runner tests to make sure they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run pytest core/reflex/tests/test_runner.py -q -p no:randomly`
Expected: all passed.

- [ ] **Step 7: Move the remaining tests onto proposals**

Mechanical — each line below becomes `ReflexProposal(decision="none")` in place of `None`; add `ReflexProposal` to that file's `from bus.schemas.events import …` line (or add `from bus.schemas.events import ReflexProposal`):

| File | Lines (as of `3be19e6`) | Change |
|---|---|---|
| `tests/core/reflex/test_passive_observation.py` | 153, 182, 436, 462 | `engine.process_event = AsyncMock(return_value=None)` → `AsyncMock(return_value=ReflexProposal(decision="none"))`. Do **not** touch `redis.set = AsyncMock(return_value=None)` lines. |
| `tests/core/reflex/test_runner_attention.py` | 51, 76 | same `process_event` change |
| `tests/core/reflex/test_availability.py` | 395, 436 | same `process_event` change |
| `tests/core/reflex/test_availability.py` | 410 | `side_effect=[ConnectionError("model down"), None, None]` → `side_effect=[ConnectionError("model down"), ReflexProposal(decision="none"), ReflexProposal(decision="none")]` |
| `tests/core/reflex/test_trigger_fired_reclaim.py` | 132 | `engine.process_trigger_fired = AsyncMock(return_value=None)` → `...ReflexProposal(decision="none"))` |
| `core/reflex/tests/test_trigger_fired_consumer.py` | 25 (fixture `mock_engine`) | same `process_trigger_fired` change |

Then the behavioural rewrites:

In `tests/core/reflex/test_passive_observation.py`, replace `test_action_path_observation_is_unchanged` (the `@pytest.mark.asyncio` at line 228 through its last assert) with:

```python
@pytest.mark.asyncio
async def test_a_proposal_skips_the_debounce() -> None:
    """Act/ask/invalid are rare and are the evidence #285 collects — never debounced."""
    from core.reflex.runner import process_stream_entry

    engine = AsyncMock()
    engine.process_event = AsyncMock(
        return_value=ReflexProposal(decision="ask", reason="Dim for the film?")
    )
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=None)  # the entity is inside its debounce window

    took_action = await process_stream_entry(
        entry_id=b"1-0",
        entry_data=_entry(_event()),
        engine=engine,
        agent=AsyncMock(),
        redis=redis,
        result_stream="alfred:home:action_results",
        observation_stream=STREAM,
    )

    assert took_action is False
    assert not any(
        str(c.args[0]).startswith(OBSERVED_ENTITY_PREFIX) for c in redis.set.await_args_list
    )
    (call,) = redis.xadd.await_args_list
    obs = ReflexObservation.model_validate_json(call.args[1]["event"])
    assert obs.proposal is not None
    assert obs.proposal.decision == "ask"
    assert obs.action is None
```

and replace `test_the_action_path_still_propagates_write_failures` (its `@pytest.mark.asyncio` through the end of its `with pytest.raises` block) with:

```python
@pytest.mark.asyncio
async def test_a_failed_proposal_write_does_not_block_the_ack() -> None:
    """Shadow recording is bookkeeping too: under maxmemory it must not wedge the loop."""
    from core.reflex.runner import process_stream_entry

    engine = AsyncMock()
    engine.process_event = AsyncMock(
        return_value=ReflexProposal(decision="ask", reason="Dim for the film?")
    )
    redis = AsyncMock()
    redis.xadd = AsyncMock(
        side_effect=Exception("OOM command not allowed when used memory > 'maxmemory'")
    )

    took_action = await process_stream_entry(
        entry_id=b"1-0",
        entry_data=_entry(_event()),
        engine=engine,
        agent=AsyncMock(),
        redis=redis,
        result_stream="alfred:home:action_results",
        observation_stream=STREAM,
    )

    assert took_action is False  # the caller ACKs; the decision was already made
```

In `core/reflex/tests/test_trigger_fired_consumer.py`, replace `test_handle_trigger_fired_with_slm_action` and `test_handle_trigger_fired_publishes_observation` (line 284 to the end of the file) with:

```python
@pytest.mark.asyncio
async def test_a_trigger_proposal_is_recorded_not_executed(
    mock_agent: AsyncMock,
    mock_publisher: AsyncMock,
) -> None:
    from bus.schemas.events import ActionRequest, ReflexObservation, ReflexProposal
    from core.reflex.__main__ import _handle_trigger_fired

    engine = AsyncMock()
    engine.process_trigger_fired = AsyncMock(
        return_value=ReflexProposal(
            decision="act",
            reason="Bedtime",
            action=ActionRequest(
                source="reflex-engine",
                target_service="home-service",
                tool_name="home.light_turn_off",
                parameters={"target": "Living Room"},
                reason="Bedtime",
            ),
        )
    )
    event = TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time")
    redis = AsyncMock()

    await _handle_trigger_fired(_make_entry_data(event), engine, mock_agent, redis, mock_publisher)

    mock_publisher.publish.assert_called_once()  # Path A is unchanged
    mock_agent.execute_action.assert_not_called()
    (call,) = redis.xadd.await_args_list
    stream, fields = call.args
    assert stream == "alfred:reflex:observations"
    obs = ReflexObservation.model_validate_json(fields["event"])
    assert obs.origin == "trigger_fired"
    assert obs.proposal is not None
    assert obs.proposal.decision == "act"
    assert obs.action is None
    assert obs.result is None
    assert redis.hincrby.await_args_list[0].args[1:] == ("act", 1)


@pytest.mark.asyncio
async def test_a_trigger_none_records_only_the_count(
    mock_engine: AsyncMock,
    mock_agent: AsyncMock,
    mock_publisher: AsyncMock,
) -> None:
    from core.reflex.__main__ import _handle_trigger_fired

    event = TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time")
    redis = AsyncMock()

    await _handle_trigger_fired(_make_entry_data(event), mock_engine, mock_agent, redis, mock_publisher)

    redis.xadd.assert_not_awaited()
    assert redis.hincrby.await_args_list[0].args[1:] == ("none", 1)
```

Run: `grep -rn "process_event = AsyncMock(return_value=None)\|process_trigger_fired = AsyncMock(return_value=None)" --include='*.py' .` — expected: no matches. Run `grep -rn "publish_observation" --include='*.py' core/reflex` — expected: only its definition in `runner.py` (its remaining caller is `core/routing/domain_router.py`).

- [ ] **Step 8: Run all Reflex tests**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run pytest core/reflex tests/core/reflex tests/integration/test_reflex_end_to_end.py bus/schemas tests/core/memory/test_ingestor_proposal.py -q -p no:randomly && uv run mypy --strict core/reflex/ shared/ bus/`
Expected: all passed; mypy `Success`.

- [ ] **Step 9: Update the docs**

`docs/architecture.md` §2:
- directly under the sentence `The full path from a physical device state change to an executed action:` add a line: `> **Shadow mode ([#285](https://github.com/anirudhlath/alfred/issues/285)):** Reflex currently stops at the decision. The \`execute_action\` steps below do not run until #286.`
- replace the bullet that begins `- If the SLM returns \`{"action": "none"}\`, no action is dispatched and the message is ACKed normally — but the event is no longer forgotten.` — keep the rest of that bullet verbatim, but change its opening to `- On **none**, the event is still not forgotten.` and insert this bullet above it:

```markdown
- Reflex runs in **shadow mode** ([#285](https://github.com/anirudhlath/alfred/issues/285)): `engine.process_event()` returns a `ReflexProposal` and the runner executes nothing. Every decision increments `alfred:reflex:decisions:<UTC date>` (one hash per day, one field per decision, 30-day TTL). An **act**, **ask** or **invalid** proposal is published as a `ReflexObservation` with `proposal` set and `action`/`result` empty, bypassing the debounce below. Both writes are best-effort: a failure is logged and the entry is still ACKed. `python -m core.reflex.shadow_report --days 7` prints them for review.
```

`core/CLAUDE.md`: replace the `runner.py` bullet with `` - `runner.py` — Event loop orchestration + `ensure_consumer_group()`, `observe_passively()`, `publish_observation()`, `publish_proposal()`, `count_decision()` (shadow mode, #285) ``.

`CLAUDE.md` (repo root), Gotchas: directly below the line `` - Import `publish_observation` from `core.reflex.runner` to publish observations from new code paths `` add `` - Reflex runs in shadow mode (#285): it executes nothing until #286. Record its decisions with `publish_proposal` and `count_decision` from `core.reflex.runner` ``.

`docs/PRD.md`: bump `Capability statuses current as of **2026-10-06**` to **2026-10-07**, and add this row to §4.4 Smart home, directly above the "Tiered autonomy" row:

```markdown
| Reflex judgment calls: the fast mind sees the time, who is home and the house by room, and decides act, ask or nothing on common sense and learned habits — recorded in shadow mode for a week of review before it acts | In review | [#285](https://github.com/anirudhlath/alfred/issues/285), spec `2026-10-07-reflex-context-design.md` |
```

- [ ] **Step 10: Commit**

```bash
git add -A shared/streams.py core/reflex tests/core/reflex docs/architecture.md core/CLAUDE.md CLAUDE.md docs/PRD.md
git commit -m "feat(reflex): shadow mode — count every decision, record proposals, execute nothing (#285)"
```

---

### Task 8: The shadow report

**Files:**
- Create: `core/reflex/shadow_report.py`
- Test: `tests/core/reflex/test_shadow_report.py`
- Docs: `core/CLAUDE.md` (one bullet)

**Interfaces:**
- Consumes: `decisions_key` (Task 7); `ReflexObservation`, `ReflexProposal` (Task 1); `shared.redis_streams.revrange(redis, stream, *, count, max_id="+", min_id="-")` (existing; calls `redis.xrevrange(stream, max=…, min=…, count=…)`); `shared.usertime.get_user_timezone`.
- Produces: `build_report(redis: AioRedis, *, days: int, now: datetime, tz_name: str) -> str`; `format_proposal(obs: ReflexObservation, tz: ZoneInfo) -> str`; module constant `MAX_SCAN = 20_000`; CLI `python -m core.reflex.shadow_report --days N`.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/reflex/test_shadow_report.py`:

```python
"""Shadow report — a week of Reflex proposals for the owner's verdicts (#285)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

from bus.schemas.events import (
    ActionRequest,
    ReflexObservation,
    ReflexProposal,
    StateChangedEvent,
    TriggerFired,
)
from core.reflex import shadow_report
from core.reflex.shadow_report import build_report

if TYPE_CHECKING:
    import pytest

NOW = datetime(2026, 10, 8, 3, 30, tzinfo=UTC)  # Wed 7 Oct, 22:30 in Chicago

ACT = ReflexProposal(
    decision="act",
    reason="Film at night",
    action=ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="home.light_turn_on",
        parameters={"target": "Living Room", "brightness_pct": 30},
    ),
)
ASK = ReflexProposal(decision="ask", reason="Dim for the film?")


def _entry(
    at: datetime, proposal: ReflexProposal | None, trigger: TriggerFired | None = None
) -> tuple[bytes, dict[bytes, bytes]]:
    if trigger is not None:
        obs = ReflexObservation(
            source="reflex-engine",
            origin="trigger_fired",
            trigger_event=trigger.model_dump(),
            proposal=proposal,
            timestamp=at,
        )
    else:
        event = StateChangedEvent(
            source="home-service",
            domain="home",
            entity_id="media_player.living_room_tv",
            old_state="paused",
            new_state="playing",
            attributes={"friendly_name": "Living Room TV"},
        )
        obs = ReflexObservation(
            source="reflex-engine",
            origin="state_change",
            trigger_event=event.model_dump(),
            proposal=proposal,
            timestamp=at,
        )
    return f"{int(at.timestamp() * 1000)}-0".encode(), {b"event": obs.model_dump_json().encode()}


def _redis(
    entries: list[tuple[bytes, dict[bytes, bytes]]],
    counts: dict[str, dict[bytes, bytes]] | None = None,
) -> AsyncMock:
    redis = AsyncMock()
    redis.xrevrange = AsyncMock(return_value=list(reversed(entries)))  # newest first
    redis.hgetall = AsyncMock(side_effect=lambda key: (counts or {}).get(key, {}))
    return redis


async def test_the_counts_table_covers_every_utc_day_in_the_window() -> None:
    redis = _redis([], {"alfred:reflex:decisions:2026-10-07": {b"act": b"1", b"none": b"40"}})

    report = await build_report(redis, days=2, now=NOW, tz_name="America/Chicago")

    assert "| 2026-10-06 | 0 | 0 | 0 | 0 |" in report
    assert "| 2026-10-07 | 1 | 0 | 40 | 0 |" in report
    assert "| 2026-10-08 | 0 | 0 | 0 | 0 |" in report


async def test_proposals_are_listed_oldest_first_in_local_time() -> None:
    redis = _redis(
        [
            _entry(datetime(2026, 10, 7, 3, 0, tzinfo=UTC), ACT),  # Tue 6 Oct 22:00 local
            _entry(datetime(2026, 10, 7, 4, 0, tzinfo=UTC), None),  # passive: left out
            _entry(datetime(2026, 10, 7, 5, 0, tzinfo=UTC), ReflexProposal(decision="none")),
            _entry(datetime(2026, 10, 8, 2, 0, tzinfo=UTC), ASK),  # Wed 7 Oct 21:00 local
        ]
    )

    report = await build_report(redis, days=2, now=NOW, tz_name="America/Chicago")

    act = (
        '- Tue 06 Oct 22:00 · **act** · Living Room TV: paused → playing — "Film at night"'
        " — home.light_turn_on(target=Living Room, brightness_pct=30)"
    )
    ask = '- Wed 07 Oct 21:00 · **ask** · Living Room TV: paused → playing — "Dim for the film?"'
    assert "### Proposals (2)" in report
    assert act in report
    assert ask in report
    assert report.index(act) < report.index(ask)


async def test_an_invalid_proposal_shows_its_problem_and_raw_text_on_one_line() -> None:
    invalid = ReflexProposal(decision="invalid", raw="```json\n{}\n```", problem="not JSON")
    redis = _redis([_entry(datetime(2026, 10, 7, 3, 0, tzinfo=UTC), invalid)])

    report = await build_report(redis, days=2, now=NOW, tz_name="America/Chicago")

    line = (
        "· **invalid** · Living Room TV: paused → playing"
        " — problem: not JSON · raw: `'''json {} '''`"
    )
    assert line in report


async def test_a_trigger_proposal_names_the_trigger() -> None:
    trigger = TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time")
    redis = _redis([_entry(datetime(2026, 10, 7, 3, 0, tzinfo=UTC), ASK, trigger)])

    report = await build_report(redis, days=2, now=NOW, tz_name="America/Chicago")

    assert "· **ask** · trigger bedtime —" in report


async def test_the_scan_starts_at_the_window() -> None:
    redis = _redis([])

    await build_report(redis, days=2, now=NOW, tz_name="UTC")

    start_ms = int(datetime(2026, 10, 6, 3, 30, tzinfo=UTC).timestamp() * 1000)
    assert redis.xrevrange.await_args.kwargs["min"] == f"{start_ms}-0"


async def test_a_full_scan_says_it_was_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shadow_report, "MAX_SCAN", 1)
    redis = _redis([_entry(datetime(2026, 10, 7, 3, 0, tzinfo=UTC), ASK)])

    report = await build_report(redis, days=2, now=NOW, tz_name="UTC")

    assert "Scanned the newest 1 observations only" in report


async def test_no_proposals_says_so_and_bad_entries_are_skipped() -> None:
    redis = _redis([(b"1-0", {b"event": b"garbage"}), (b"2-0", {b"other": b"x"})])

    report = await build_report(redis, days=1, now=NOW, tz_name="UTC")

    assert "### Proposals (0)" in report
    assert "None in this window." in report
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/core/reflex/test_shadow_report.py -q -p no:randomly`
Expected: FAIL — `ImportError: cannot import name 'shadow_report'`.

- [ ] **Step 3: Implement the report**

Create `core/reflex/shadow_report.py`:

```python
"""Shadow report: what Reflex proposed but did not do (#285).

    docker exec alfred python -m core.reflex.shadow_report --days 7

Prints Markdown for the #285 thread: daily decision counts from
``alfred:reflex:decisions:<UTC date>``, then every act, ask and invalid proposal in
the window, oldest first, in the user's local time.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from bus.schemas.events import ReflexObservation
from core.reflex.runner import decisions_key
from shared.config import AlfredConfig
from shared.redis_streams import create_redis, revrange
from shared.streams import REFLEX_OBSERVATIONS_STREAM, decode_stream_value
from shared.usertime import get_user_timezone

if TYPE_CHECKING:
    from zoneinfo import ZoneInfo

    from shared.types import AioRedis

DECISIONS: tuple[str, ...] = ("act", "ask", "none", "invalid")
# Upper bound on observations scanned. A week runs to a few thousand; past this the
# report says it was truncated rather than silently dropping the oldest.
MAX_SCAN = 20_000
MAX_RAW_CHARS = 200


def _describe(obs: ReflexObservation) -> str:
    event = obs.trigger_event
    if obs.origin == "trigger_fired":
        return f"trigger {event.get('trigger_name', '?')}"
    attributes = event.get("attributes")
    name = attributes.get("friendly_name") if isinstance(attributes, dict) else None
    label = name if isinstance(name, str) and name else event.get("entity_id", "?")
    return f"{label}: {event.get('old_state')} → {event.get('new_state')}"


def format_proposal(obs: ReflexObservation, tz: ZoneInfo) -> str:
    """One Markdown bullet for one proposal observation."""
    proposal = obs.proposal
    if proposal is None:
        raise ValueError("format_proposal needs an observation that carries a proposal")
    when = obs.timestamp.astimezone(tz).strftime("%a %d %b %H:%M")
    head = f"- {when} · **{proposal.decision}** · {_describe(obs)}"
    if proposal.decision == "invalid":
        raw = " ".join((proposal.raw or "").split()).replace("`", "'")[:MAX_RAW_CHARS]
        return f"{head} — problem: {proposal.problem} · raw: `{raw}`"
    parts = [head]
    if proposal.reason:
        parts.append(f'"{proposal.reason}"')
    if proposal.action is not None:
        params = ", ".join(f"{k}={v}" for k, v in proposal.action.parameters.items())
        parts.append(f"{proposal.action.tool_name}({params})")
    return " — ".join(parts)


async def build_report(redis: AioRedis, *, days: int, now: datetime, tz_name: str) -> str:
    """Markdown: per-day decision counts, then every non-none proposal in the window."""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(tz_name)
    start = now - timedelta(days=days)
    lines = [
        f"## Reflex shadow report: the last {days} days (times in {tz_name})",
        "",
        "| Day (UTC) | act | ask | none | invalid |",
        "|---|---|---|---|---|",
    ]
    day = start.astimezone(UTC).date()
    while day <= now.astimezone(UTC).date():
        raw_counts = await redis.hgetall(decisions_key(day))
        counts = {decode_stream_value(k): decode_stream_value(v) for k, v in raw_counts.items()}
        cells = " | ".join(counts.get(d, "0") for d in DECISIONS)
        lines.append(f"| {day.isoformat()} | {cells} |")
        day += timedelta(days=1)

    entries = await revrange(
        redis,
        REFLEX_OBSERVATIONS_STREAM,
        count=MAX_SCAN,
        min_id=f"{int(start.timestamp() * 1000)}-0",
    )
    proposals: list[ReflexObservation] = []
    for _entry_id, fields in reversed(entries):  # oldest first
        raw_event = fields.get(b"event") or fields.get("event")
        if raw_event is None:
            continue
        try:
            obs = ReflexObservation.model_validate_json(decode_stream_value(raw_event))
        except ValueError:
            continue
        if obs.proposal is not None and obs.proposal.decision != "none":
            proposals.append(obs)

    lines += ["", f"### Proposals ({len(proposals)})", ""]
    lines += [format_proposal(obs, tz) for obs in proposals] or ["None in this window."]
    if len(entries) >= MAX_SCAN:
        lines += [
            "",
            f"_Scanned the newest {MAX_SCAN} observations only; older ones in the window"
            " are not shown._",
        ]
    return "\n".join(lines)


async def _main(days: int) -> None:
    config = AlfredConfig.from_env()
    redis = create_redis(config.redis_url)
    try:
        tz_name = await get_user_timezone(redis)
        print(await build_report(redis, days=days, now=datetime.now(UTC), tz_name=tz_name))
    finally:
        await redis.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Reflex shadow report (#285)")
    parser.add_argument("--days", type=int, default=7, help="window length (default 7)")
    args = parser.parse_args()
    asyncio.run(_main(args.days))


if __name__ == "__main__":
    main()
```

If mypy flags `await redis.hgetall(...)` (`Awaitable | dict`), add `# type: ignore[misc,unused-ignore]` on that line, as `shared/redis_streams.py` does for `xrevrange`.

- [ ] **Step 4: Run the tests to make sure they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/core/reflex/test_shadow_report.py -q -p no:randomly && uv run mypy --strict core/reflex/shadow_report.py`
Expected: all passed; mypy `Success`.

- [ ] **Step 5: Document it and commit**

`core/CLAUDE.md`: add after the `runner.py` bullet `` - `shadow_report.py` — `python -m core.reflex.shadow_report --days N`: Markdown of daily decision counts and every act/ask/invalid proposal, for #285's shadow review ``.

```bash
git add core/reflex/shadow_report.py tests/core/reflex/test_shadow_report.py core/CLAUDE.md
git commit -m "feat(reflex): shadow report of a week's proposals for review (#285)"
```

---

### Task 9: Full gate and hand-off

**Files:** none new.

- [ ] **Step 1: Run the full gate once**

```bash
cd ~/code/.worktrees/alfred/reflex-context
uv run ruff check . && uv run ruff format --check . \
  && uv run mypy --strict alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/ \
  && PYTHONDONTWRITEBYTECODE=1 uv run pytest -q -p no:randomly
```
Expected: all green. Fix anything red in the task that owns it, re-run only the narrow tests, then this gate once more.

- [ ] **Step 2: Hygiene check before the first push**

Run the two greps in `PWA-EXPOSURE-RUNBOOK.md` §Secret hygiene (kept outside the repo, because quoting them commits the literals they hunt for) over `git diff origin/master...HEAD` and the branch's commit messages, and check the diff for household names and addresses.
Expected: nothing found. (Placeholder names only; see Global Constraints.)

- [ ] **Step 3: Finish the branch**

Invoke `superpowers:finishing-a-development-branch`. The PR targets `master`, closes nothing (it is slice 1 of #285 → #286 → #287; say `Refs #285`), and its body lists the post-merge checks below. **Merging deploys; the owner merges.**

## After merge (owner deploys; read-only checks, posted to #285)

These come from the spec's Measurement section and are not code tasks:

1. **First full day:**
   - Reflex prompt tokens from the `@track_tokens` records for `openai_client.infer`, target ~1,000.
   - `reflex.p50_ms` on the admin overview, target under 500.
   - Event → observation p50 split by the 5-minute rule.
   - Decision totals from `alfred:reflex:decisions:<date>`.
2. **After a week:** `docker exec alfred python -m core.reflex.shadow_report --days 7`, reviewed with the owner privately (it names people and their comings and goings); only the counts and verdict tallies go to #285.
3. **When alfred-home-service#25 deploys:** confirm the House section groups by room in a captured prompt.
