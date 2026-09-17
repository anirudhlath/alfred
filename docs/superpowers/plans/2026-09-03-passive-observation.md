# Passive Observation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record the home state changes Alfred sees and chooses not to act on, so episodic memory and the Librarian's pattern detection have data to work with.

**Architecture:** Three changes, no new service. `ReflexObservation` gains optional `action`/`result` so "I saw this and did nothing" is representable. The Reflex runner publishes a debounced observation on the no-action path instead of dropping the event. The Memory Ingestor recognises `action is None`, writes the entry with `source="observation"`, and scores it against a separate entity-frequency key so passive volume cannot flatten novelty for real reflex actions.

**Tech Stack:** Python 3.12, Pydantic v2, redis.asyncio, pytest + pytest-asyncio, loguru (memory/ingestor) and stdlib logging (reflex runner).

**Spec:** `docs/superpowers/specs/2026-09-03-passive-observation-design.md`

---

## Before you start

**Worktree.** The spec lives on branch `docs/passive-observation-spec` (PR #191). Implementation gets its own branch off `master`:

```bash
cd ~/code/alfred-deploy/alfred
git fetch origin
git worktree add ~/code/.worktrees/alfred/passive-observation -b feat/passive-observation origin/master
cd ~/code/.worktrees/alfred/passive-observation
uv sync
```

**Running tests.** Always `uv run pytest`, never bare `pytest`. Test paths are configured in `pyproject.toml` (`testpaths = ["core", "bus", "domains", "telemetry", "sdk", "tests"]`), so note that reflex tests live in **two** places: `core/reflex/tests/` (older) and `tests/core/reflex/` (newer). Both run. New tests go under `tests/`.

**Logging style differs by module.** `core/reflex/runner.py` uses **stdlib logging** — printf placeholders (`%s`). `core/memory/ingestor.py` uses **loguru** — brace placeholders (`{}`). Mixing them up prints the literal template in production; that exact bug was fixed in PR #190. Match the file you are editing.

**Commit style.** Conventional commits. The `pr-title` CI check enforces the same format on the PR title.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `bus/schemas/events.py` | Modify (~line 152) | `ReflexObservation.action` / `.result` become optional |
| `shared/streams.py` | Modify (~line 34) | Two new key constants |
| `core/reflex/runner.py` | Modify | `observe_passively()` + call it on the no-action path |
| `core/memory/significance.py` | Modify | `frequency_key` constructor argument |
| `core/memory/ingestor.py` | Modify | Passive branch: summary, semantic key, entities, scorer selection |
| `core/memory/ingestor_main.py` | Modify (~line 52) | Build the passive scorer with the observed frequency key |
| `tests/bus/test_reflex_observation_optional.py` | Create | Schema round-trips with `action=None` |
| `tests/core/reflex/test_passive_observation.py` | Create | Debounce, gating, payload shape |
| `tests/core/memory/test_passive_ingestion.py` | Create | Passive entry construction + scorer selection |
| `core/reflex/tests/test_runner.py` | Modify (~line 112, 116-145) | Narrow `.action` for mypy; the no-action test asserts the old drop behaviour |
| `bus/schemas/tests/test_events.py` | Modify (~line 272-280) | Narrow `.action` / `.result` — inside a `strict` mypy target |
| `core/reflex/tests/test_trigger_fired_consumer.py` | Modify (~line 380-381) | Same narrowing |
| `tests/core/memory/test_significance.py` | Modify (append) | Frequency-key isolation |

---

## Task 1: Make an observation able to describe a non-action

**Files:**
- Modify: `bus/schemas/events.py:138-154`
- Test: `tests/bus/test_reflex_observation_optional.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/bus/test_reflex_observation_optional.py`:

```python
"""ReflexObservation must be able to represent 'saw it, did nothing'."""

from __future__ import annotations

from bus.schemas.events import ActionRequest, ActionResult, ReflexObservation


def test_observation_without_action_round_trips() -> None:
    obs = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event={"entity_id": "light.hallway", "new_state": "on"},
    )

    assert obs.action is None
    assert obs.result is None

    restored = ReflexObservation.model_validate_json(obs.model_dump_json())
    assert restored.action is None
    assert restored.result is None
    assert restored.trigger_event["entity_id"] == "light.hallway"


def test_observation_with_action_still_round_trips() -> None:
    """Regression: the existing action path is unchanged."""
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
    obs = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event={"entity_id": "light.hallway", "new_state": "on"},
        action=action,
        result=result,
    )

    restored = ReflexObservation.model_validate_json(obs.model_dump_json())
    assert restored.action is not None
    assert restored.action.tool_name == "home.light_turn_on"
    assert restored.result is not None
    assert restored.result.status == "success"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/bus/test_reflex_observation_optional.py -v
```

Expected: `test_observation_without_action_round_trips` FAILS with a Pydantic `ValidationError` — `action: Field required`, `result: Field required`. The second test passes already.

- [ ] **Step 3: Make the fields optional**

In `bus/schemas/events.py`, replace lines 152-153:

```python
    action: ActionRequest
    result: ActionResult
```

with:

```python
    # None means: this event was seen, considered, and no action was taken.
    # Passive observations exist so pattern detection has something to read;
    # without them Alfred only remembers what it did, never what it saw.
    action: ActionRequest | None = None
    result: ActionResult | None = None
```

Also update the class docstring (lines 139-144) — it currently claims an observation is published "after every Reflex action execution":

```python
    """A structured observation of a Reflex Engine event for System 2 awareness.

    Published after a Reflex action executes, and also when the Reflex
    Engine considers an event and takes no action (``action is None``).
    The Memory Ingestor consumes these and writes them to episodic memory
    so that the Conscious Engine can recall them during context assembly.
    """
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/bus/test_reflex_observation_optional.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Narrow the existing dereferences that mypy now rejects**

`mypy` runs `strict` over `bus/` and `core/` — which includes the test files that live inside those trees. Three of them dereference `.action` / `.result` directly and now fail with `Item "None" of "ActionRequest | None" has no attribute ...`. Run it first to see them:

```bash
uv run mypy bus/ core/
```

Expected: 6 errors across 3 files. Fix each by asserting the narrowing, which is also a meaningful assertion in these action-path tests.

In `bus/schemas/tests/test_events.py`, replace lines 272-273:

```python
    assert obs.action.tool_name == "lighting.dim_lights"
    assert obs.result.status == "success"
```

with:

```python
    assert obs.action is not None
    assert obs.action.tool_name == "lighting.dim_lights"
    assert obs.result is not None
    assert obs.result.status == "success"
```

and line 280:

```python
    assert restored.action.tool_name == "lighting.dim_lights"
```

with:

```python
    assert restored.action is not None
    assert restored.action.tool_name == "lighting.dim_lights"
```

In `core/reflex/tests/test_runner.py`, replace line 112:

```python
    assert obs.action.tool_name == "smart_home.turn_on"
```

with:

```python
    assert obs.action is not None
    assert obs.action.tool_name == "smart_home.turn_on"
```

In `core/reflex/tests/test_trigger_fired_consumer.py`, replace lines 380-381:

```python
    assert obs.action.tool_name == "lighting.dim_lights"
    assert obs.result.status == "success"
```

with:

```python
    assert obs.action is not None
    assert obs.action.tool_name == "lighting.dim_lights"
    assert obs.result is not None
    assert obs.result.status == "success"
```

`tests/integration/test_reflex_observation_pipeline.py:97-98` dereferences the same way but is **not** mypy-covered (CI checks `alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/`, not `tests/`). It passes at runtime — leave it alone.

- [ ] **Step 6: Verify types and tests are clean**

```bash
uv run mypy bus/ core/
uv run pytest tests/bus bus core/reflex -q
```

Expected: mypy clean, all tests pass. (`core/reflex/tests/test_runner.py::test_process_stream_entry_no_action` still passes here — it breaks in Task 4, deliberately.)

- [ ] **Step 7: Commit**

```bash
git add bus/schemas/events.py bus/schemas/tests/test_events.py \
        core/reflex/tests/test_runner.py core/reflex/tests/test_trigger_fired_consumer.py \
        tests/bus/test_reflex_observation_optional.py
git commit -m "feat(schemas): allow ReflexObservation to describe a non-action"
```

---

## Task 2: Add the Redis key constants

**Files:**
- Modify: `shared/streams.py:34`

No test of its own — these are consumed by Tasks 3 and 5, whose tests assert the exact key strings.

- [ ] **Step 1: Add the constants**

In `shared/streams.py`, replace line 34:

```python
ENTITY_FREQUENCY_KEY = "alfred:entity:freq"
```

with:

```python
ENTITY_FREQUENCY_KEY = "alfred:entity:freq"
# Passive observations are scored against their own frequency population.
# Sharing ENTITY_FREQUENCY_KEY would drive every count high enough that
# novelty (1/count) collapses to ~0 for real reflex actions too.
OBSERVED_FREQUENCY_KEY = "alfred:entity:freq:observed"

# Per-entity debounce for passive observation (SET NX EX). One noisy device
# accounted for 64% of qualifying events on the live instance, so this is
# what keeps episodic memory readable.
OBSERVED_ENTITY_PREFIX = "alfred:observer:seen:"
```

- [ ] **Step 2: Verify the module still imports**

```bash
uv run python -c "from shared.streams import OBSERVED_FREQUENCY_KEY, OBSERVED_ENTITY_PREFIX; print(OBSERVED_FREQUENCY_KEY, OBSERVED_ENTITY_PREFIX)"
```

Expected output: `alfred:entity:freq:observed alfred:observer:seen:`

- [ ] **Step 3: Commit**

```bash
git add shared/streams.py
git commit -m "feat(streams): add passive observation frequency and debounce keys"
```

---

## Task 3: `observe_passively()` with a per-entity debounce

**Files:**
- Modify: `core/reflex/runner.py`
- Test: `tests/core/reflex/test_passive_observation.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/core/reflex/test_passive_observation.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/core/reflex/test_passive_observation.py -v
```

Expected: all 7 FAIL at import — `ImportError: cannot import name 'observe_passively' from 'core.reflex.runner'`.

- [ ] **Step 3: Implement `observe_passively`**

In `core/reflex/runner.py`, add `import os` to the stdlib imports (after `import logging` on line 9):

```python
import logging
import os
```

Add `StateChangedEvent`-typed import needs nothing new — it is already imported on line 14. Add the constant import to line 15:

```python
from shared.streams import OBSERVED_ENTITY_PREFIX, decode_stream_value
```

After the `logger = logging.getLogger(__name__)` line (line 29), add:

> **As-built (2026-09-03).** This step originally read
> `OBSERVATION_DEBOUNCE_SECONDS = int(os.getenv("OBSERVATION_DEBOUNCE_SECONDS", "300"))`.
> That bare parse was replaced during implementation (`a1462dd`, `790a169`) and the
> snippet below is what shipped. **Do not revert it.** `core.reflex.runner` is imported
> at module scope by several unrelated services (for `ensure_consumer_group`), so a
> malformed `.env` value would raise `ValueError` at import and take all of them down.
> The clamp is separate: `redis.set(..., ex=0)` is rejected outright by Redis, so a
> below-minimum value had to become 1 rather than 0 — and it warns, because silently
> clamping made "set it to 0 to disable" look like it worked.

```python
def _debounce_default() -> int:
    """Read the debounce window from the env, tolerating garbage.

    This module is imported at module scope by several unrelated services
    (for ``ensure_consumer_group``), so a malformed value must never raise
    at import time and take them down with it.
    """
    raw = os.getenv("OBSERVATION_DEBOUNCE_SECONDS", "").strip()
    if not raw:
        return 300
    try:
        seconds = int(raw)
    except ValueError:
        logger.warning("Invalid OBSERVATION_DEBOUNCE_SECONDS %r — using 300", raw)
        return 300
    if seconds < 1:
        # Silently clamping made "0 to disable" look like it worked while
        # actually recording on a 1-second window.
        logger.warning(
            "OBSERVATION_DEBOUNCE_SECONDS %r is below the 1s minimum — clamping to 1. "
            "Passive observation cannot be disabled this way.",
            raw,
        )
        return 1
    return seconds


# Per-entity window for passive observation. Deliberately separate from the
# attention gate's 5-second cooldown: that one asks "is this SLM call worth
# making", this one asks "is this worth remembering". Values differ by two
# orders of magnitude. Tunable without a rebuild — see the review point in
# docs/superpowers/specs/2026-09-03-passive-observation-design.md.
OBSERVATION_DEBOUNCE_SECONDS = _debounce_default()
```

Then add the function directly after `publish_observation` (after line 48):

```python
async def observe_passively(
    redis: AioRedis,
    stream: str,
    event: StateChangedEvent,
    debounce_seconds: int = OBSERVATION_DEBOUNCE_SECONDS,
) -> bool:
    """Record an event the Reflex Engine saw but took no action on.

    Debounced per entity so one flapping device cannot flood episodic
    memory. Returns True if an observation was published.
    """
    seen_key = f"{OBSERVED_ENTITY_PREFIX}{event.entity_id}"
    if not await redis.set(seen_key, "1", nx=True, ex=debounce_seconds):
        logger.debug("Observation debounced: %s", event.entity_id)
        return False

    observation = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event=event.model_dump(),
    )
    await redis.xadd(stream, {"event": observation.model_dump_json()})
    logger.debug(
        "Observed: %s (%s → %s)", event.entity_id, event.old_state, event.new_state
    )
    return True
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/core/reflex/test_passive_observation.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add core/reflex/runner.py tests/core/reflex/test_passive_observation.py
git commit -m "feat(reflex): add debounced passive observation publisher"
```

---

## Task 4: Publish on the no-action path

`observe_passively` exists but nothing calls it. This task wires it in — and deliberately inverts an existing test that asserts the old drop behaviour.

**Files:**
- Modify: `core/reflex/runner.py:108-111`
- Modify: `core/reflex/tests/test_runner.py:116-145`
- Test: `tests/core/reflex/test_passive_observation.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/reflex/test_passive_observation.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/core/reflex/test_passive_observation.py -v
```

Expected: `test_no_action_path_publishes_an_observation` FAILS with `AssertionError: Expected 'xadd' to have been awaited once. Awaited 0 times.` The other three pass already (they describe behaviour that is either unchanged or coincidentally correct).

- [ ] **Step 3: Wire it into the runner**

In `core/reflex/runner.py`, replace lines 109-111:

```python
    if action is None:
        logger.debug("No action for event %s", event.entity_id)
        return False
```

with:

```python
    if action is None:
        # Record it rather than dropping it. Without this Alfred remembers
        # only what it did, never what it saw, and pattern detection has
        # nothing to run over.
        await observe_passively(redis, observation_stream, event)
        return False
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/core/reflex/test_passive_observation.py -v
```

Expected: 11 passed.

- [ ] **Step 5: Fix the test that encoded the old behaviour**

`uv run pytest core/reflex/tests/test_runner.py -v` now fails on `test_process_stream_entry_no_action` — it asserts `mock_redis.xadd.assert_not_called()`, which is exactly the drop we are removing.

In `core/reflex/tests/test_runner.py`, replace the body of that test (lines 115-145) with:

```python
@pytest.mark.asyncio
async def test_process_stream_entry_no_action_records_an_observation() -> None:
    """An event the SLM ignores is recorded passively, not dropped."""
    from core.reflex.runner import process_stream_entry

    event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="sensor.temperature",
        new_state="22.5",
    )

    mock_engine = AsyncMock()
    mock_engine.process_event.return_value = None

    mock_agent = AsyncMock()
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(return_value=True)

    result = await process_stream_entry(
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
```

- [ ] **Step 6: Run the full reflex suite**

```bash
uv run pytest core/reflex tests/core/reflex -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add core/reflex/runner.py core/reflex/tests/test_runner.py tests/core/reflex/test_passive_observation.py
git commit -m "feat(reflex): record events the SLM sees but does not act on"
```

---

## Task 5: Give the scorer its own frequency key

`_score_novelty` calls `ZINCRBY` on `alfred:entity:freq` and derives novelty as `1/count`. ~250 passive observations a day would drive every count high enough that novelty collapses to ~0 for genuine reflex actions too.

**Files:**
- Modify: `core/memory/significance.py:16-24, 61-78`
- Test: `tests/core/memory/test_significance.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/core/memory/test_significance.py`:

```python
# ---------------------------------------------------------------------------
# Frequency-key isolation for passive observations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scorer_defaults_to_the_shared_frequency_key(
    mock_redis: AsyncMock, config: AlfredConfig
) -> None:
    """Existing callers keep the old behaviour."""
    scorer = SignificanceScorer(redis=mock_redis, config=config)
    await scorer._score_novelty(_make_entry(entities=["light.hallway"]))
    assert mock_redis.zincrby.await_args.args[0] == ENTITY_FREQUENCY_KEY


@pytest.mark.asyncio
async def test_scorer_can_use_a_separate_frequency_key(
    mock_redis: AsyncMock, config: AlfredConfig
) -> None:
    """Passive observations must not contaminate the reflex-action population."""
    from shared.streams import OBSERVED_FREQUENCY_KEY

    scorer = SignificanceScorer(
        redis=mock_redis, config=config, frequency_key=OBSERVED_FREQUENCY_KEY
    )
    await scorer._score_novelty(
        _make_entry(source="observation", entities=["light.hallway"])
    )

    keys_used = {c.args[0] for c in mock_redis.zincrby.await_args_list}
    assert keys_used == {OBSERVED_FREQUENCY_KEY}
    assert ENTITY_FREQUENCY_KEY not in keys_used
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/core/memory/test_significance.py -k frequency_key -v
```

Expected: `test_scorer_can_use_a_separate_frequency_key` FAILS with `TypeError: SignificanceScorer.__init__() got an unexpected keyword argument 'frequency_key'`. The default-behaviour test passes.

- [ ] **Step 3: Add the constructor argument**

In `core/memory/significance.py`, replace the constructor (lines 22-24):

```python
    def __init__(self, redis: AioRedis, config: AlfredConfig) -> None:
        self._redis = redis
        self._config = config
```

with:

```python
    def __init__(
        self,
        redis: AioRedis,
        config: AlfredConfig,
        frequency_key: str = ENTITY_FREQUENCY_KEY,
    ) -> None:
        self._redis = redis
        self._config = config
        # Passive observations are scored against their own population — see
        # OBSERVED_FREQUENCY_KEY. Sharing one key would collapse novelty for
        # everything, because novelty is 1/count.
        self._frequency_key = frequency_key
```

And in `_score_novelty`, replace line 69:

```python
            count = await self._redis.zincrby(ENTITY_FREQUENCY_KEY, 1, entity)
```

with:

```python
            count = await self._redis.zincrby(self._frequency_key, 1, entity)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/core/memory/test_significance.py -v
```

Expected: all pass, including the pre-existing `test_entity_frequency_tracked_on_zincrby` (the default is unchanged).

- [ ] **Step 5: Commit**

```bash
git add core/memory/significance.py tests/core/memory/test_significance.py
git commit -m "feat(memory): let SignificanceScorer target a specific frequency key"
```

---

## Task 6: Ingest passive observations as their own source

**Files:**
- Modify: `core/memory/ingestor.py:33-84`
- Test: `tests/core/memory/test_passive_ingestion.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/core/memory/test_passive_ingestion.py`:

```python
"""Ingesting observations that record a non-action."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from bus.schemas.events import ReflexObservation

if TYPE_CHECKING:
    from core.memory.schemas import EpisodicEntry


def _passive(
    entity_id: str = "media_player.living_room_apple_tv",
    old_state: str = "paused",
    new_state: str = "playing",
    attributes: dict[str, object] | None = None,
) -> ReflexObservation:
    return ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event={
            "entity_id": entity_id,
            "old_state": old_state,
            "new_state": new_state,
            "attributes": attributes if attributes is not None else {},
        },
    )


@pytest.fixture
def scorer() -> AsyncMock:
    mock = AsyncMock()
    mock.score = AsyncMock(
        return_value=MagicMock(overall=0.2, safety=0.0, novelty=0.3, personal=0.3, emotional=0.2)
    )
    return mock


@pytest.mark.asyncio
async def test_passive_observation_uses_the_observation_source(scorer: AsyncMock) -> None:
    from core.memory.ingestor import ingest_observation

    episodic = AsyncMock()
    await ingest_observation(_passive(), episodic, scorer)

    entry: EpisodicEntry = episodic.write.call_args.args[0]
    assert entry.source == "observation"


@pytest.mark.asyncio
async def test_summary_describes_the_transition(scorer: AsyncMock) -> None:
    from core.memory.ingestor import ingest_observation

    episodic = AsyncMock()
    await ingest_observation(
        _passive(attributes={"media_title": "Harry Potter"}), episodic, scorer
    )

    entry: EpisodicEntry = episodic.write.call_args.args[0]
    assert entry.summary == (
        "[observation] media_player.living_room_apple_tv: "
        "paused → playing (media_title=Harry Potter)"
    )


@pytest.mark.asyncio
async def test_summary_without_salient_attributes(scorer: AsyncMock) -> None:
    from core.memory.ingestor import ingest_observation

    episodic = AsyncMock()
    await ingest_observation(_passive("binary_sensor.front_door", "off", "on"), episodic, scorer)

    entry: EpisodicEntry = episodic.write.call_args.args[0]
    assert entry.summary == "[observation] binary_sensor.front_door: off → on"


@pytest.mark.asyncio
async def test_salient_attributes_are_folded_in_declared_order(scorer: AsyncMock) -> None:
    from core.memory.ingestor import ingest_observation

    episodic = AsyncMock()
    await ingest_observation(
        _passive(
            "light.kitchen",
            "off",
            "on",
            attributes={
                "friendly_name": "Kitchen Light",
                "brightness": 178,
                "unrelated": "ignored",
            },
        ),
        episodic,
        scorer,
    )

    entry: EpisodicEntry = episodic.write.call_args.args[0]
    assert entry.summary == (
        "[observation] light.kitchen: off → on (brightness=178, friendly_name=Kitchen Light)"
    )
    assert "ignored" not in entry.summary


@pytest.mark.asyncio
async def test_missing_old_state_is_rendered_as_unknown(scorer: AsyncMock) -> None:
    """StateChangedEvent.old_state is Optional — a first sighting has none."""
    from core.memory.ingestor import ingest_observation

    episodic = AsyncMock()
    obs = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event={"entity_id": "sensor.new_device", "new_state": "22.5"},
    )
    await ingest_observation(obs, episodic, scorer)

    entry: EpisodicEntry = episodic.write.call_args.args[0]
    assert entry.summary == "[observation] sensor.new_device: unknown → 22.5"


@pytest.mark.asyncio
async def test_entities_come_from_the_trigger_event(scorer: AsyncMock) -> None:
    from core.memory.ingestor import ingest_observation

    episodic = AsyncMock()
    await ingest_observation(_passive("light.hallway"), episodic, scorer)

    entry: EpisodicEntry = episodic.write.call_args.args[0]
    assert entry.entities == ["light.hallway"]


@pytest.mark.asyncio
async def test_semantic_key_is_searchable(scorer: AsyncMock) -> None:
    from core.memory.ingestor import ingest_observation

    episodic = AsyncMock()
    await ingest_observation(_passive("light.hallway", "off", "on"), episodic, scorer)

    entry: EpisodicEntry = episodic.write.call_args.args[0]
    assert entry.semantic_key == "Observed light.hallway change from off to on"


@pytest.mark.asyncio
async def test_passive_scorer_is_used_when_supplied(scorer: AsyncMock) -> None:
    """The passive population must be scored by the passive scorer."""
    from core.memory.ingestor import ingest_observation

    episodic = AsyncMock()
    passive_scorer = AsyncMock()
    passive_scorer.score = AsyncMock(
        return_value=MagicMock(overall=0.1, safety=0.0, novelty=0.1, personal=0.3, emotional=0.2)
    )

    await ingest_observation(_passive(), episodic, scorer, passive_scorer=passive_scorer)

    passive_scorer.score.assert_awaited_once()
    scorer.score.assert_not_awaited()


@pytest.mark.asyncio
async def test_action_observations_still_use_the_default_scorer(scorer: AsyncMock) -> None:
    """Regression: the action path is untouched by the passive scorer."""
    from bus.schemas.events import ActionRequest, ActionResult
    from core.memory.ingestor import ingest_observation

    action = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="home.light_turn_on",
        parameters={"entity_id": "light.hallway"},
    )
    obs = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event={"entity_id": "light.hallway", "new_state": "on"},
        action=action,
        result=ActionResult(
            source="home-service",
            request_id=action.request_id,
            tool_name="home.light_turn_on",
            status="success",
        ),
    )

    episodic = AsyncMock()
    passive_scorer = AsyncMock()

    await ingest_observation(obs, episodic, scorer, passive_scorer=passive_scorer)

    entry: EpisodicEntry = episodic.write.call_args.args[0]
    assert entry.source == "reflex"
    assert "home.light_turn_on" in entry.summary
    scorer.score.assert_awaited_once()
    passive_scorer.score.assert_not_awaited()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/core/memory/test_passive_ingestion.py -v
```

Expected: the passive tests FAIL with `AttributeError: 'NoneType' object has no attribute 'parameters'` — `_build_summary` dereferences `obs.action` unconditionally. `test_passive_scorer_is_used_when_supplied` fails with `TypeError: ingest_observation() got an unexpected keyword argument 'passive_scorer'`.

- [ ] **Step 3: Implement the passive branch**

In `core/memory/ingestor.py`, add the salient-attribute tuple after the `CONSUMER = "worker-1"` line (line 30):

```python
GROUP = "memory-ingestor"
CONSUMER = "worker-1"

# Folded into passive observation summaries so the consolidation LLM has
# something to correlate on beyond the bare state transition.
SALIENT_ATTRIBUTES = ("media_title", "brightness", "temperature", "friendly_name")
```

Add these three helpers after `_extract_entities` (after line 62):

> **As-built (2026-09-03).** Three corrections were made to this snippet during
> implementation and are already folded in below; the tests above assert the shipped
> behaviour. (1) Salient attributes render as `key=value`, not bare values (`b99b1c0`) —
> a lone `178` or `0` is uninterpretable to the consolidation LLM, which sees only
> `- {summary}`, and is close to noise in the embedding. (2) `_transition` treats only
> `None` as missing, not any falsy value: a sensor reading of `0` or an empty string is a
> real state and must not be rewritten to `"unknown"`. (3) `attributes` is
> `isinstance`-checked rather than `or {}`, because `trigger_event` is an untyped
> `dict[str, Any]` off the wire and a non-dict there would crash the ingest loop.

```python
def _transition(obs: ReflexObservation) -> tuple[str, str, str]:
    """Entity, old state, new state — from the raw trigger_event dict.

    Absent values become "unknown"; falsy ones do not. A sensor reading of ``0``
    or an empty string is a real state, and rewriting it matches neither the
    salient-attribute filter (which deliberately keeps a ``0``) nor the truth.
    """
    event = obs.trigger_event

    def _state(key: str) -> str:
        value = event.get(key)
        return "unknown" if value is None else str(value)

    return _state("entity_id"), _state("old_state"), _state("new_state")


def _build_observation_summary(obs: ReflexObservation) -> str:
    """Summarise a state change nobody acted on.

    Salient attributes are rendered ``key=value``. A bare ``178`` or ``0`` is
    uninterpretable to the consolidation LLM, which sees only ``- {summary}``,
    and is close to noise in the embedding.
    """
    entity, old_state, new_state = _transition(obs)
    attributes = obs.trigger_event.get("attributes")
    if not isinstance(attributes, dict):
        attributes = {}
    salient = [
        f"{key}={attributes[key]}"
        for key in SALIENT_ATTRIBUTES
        if attributes.get(key) not in (None, "")
    ]
    suffix = f" ({', '.join(salient)})" if salient else ""
    return f"[observation] {entity}: {old_state} → {new_state}{suffix}"


def _build_observation_semantic_key(obs: ReflexObservation) -> str:
    """Build a semantic key optimised for vector search over passive observations."""
    entity, old_state, new_state = _transition(obs)
    return f"Observed {entity} change from {old_state} to {new_state}"
```

`_build_summary` and `_build_semantic_key` dereference `obs.action` and `obs.result` unconditionally. Now that both are optional, strict mypy rejects them — and narrowing in the caller does not propagate across a function boundary. Take the narrowed values as parameters instead. Replace lines 33-47:

```python
def _build_summary(obs: ReflexObservation) -> str:
    """Build a human-readable summary for embedding."""
    params_str = ", ".join(f"{k}={v}" for k, v in obs.action.parameters.items())
    base = f"[reflex:{obs.origin}] {obs.action.tool_name}({params_str}) → {obs.result.status}"
    if obs.decision_context:
        base += f" | reason: {obs.decision_context}"
    return base


def _build_semantic_key(obs: ReflexObservation) -> str:
    """Build a semantic key optimised for vector search."""
    param_vals = (
        [str(v) for v in obs.action.parameters.values()] if obs.action.parameters else ["unknown"]
    )
    return f"Reflex {obs.origin} action: {obs.action.tool_name} on {', '.join(param_vals)}"
```

with:

```python
def _build_summary(obs: ReflexObservation, action: ActionRequest, result: ActionResult) -> str:
    """Build a human-readable summary for embedding."""
    params_str = ", ".join(f"{k}={v}" for k, v in action.parameters.items())
    base = f"[reflex:{obs.origin}] {action.tool_name}({params_str}) → {result.status}"
    if obs.decision_context:
        base += f" | reason: {obs.decision_context}"
    return base


def _build_semantic_key(obs: ReflexObservation, action: ActionRequest) -> str:
    """Build a semantic key optimised for vector search."""
    param_vals = (
        [str(v) for v in action.parameters.values()] if action.parameters else ["unknown"]
    )
    return f"Reflex {obs.origin} action: {action.tool_name} on {', '.join(param_vals)}"
```

Add the two types to the `TYPE_CHECKING` block (after line 25, `from core.memory.episodic.memory import EpisodicMemory`):

```python
if TYPE_CHECKING:
    import asyncio

    from bus.schemas.events import ActionRequest, ActionResult
    from core.memory.episodic.memory import EpisodicMemory
    from core.memory.significance import SignificanceScorer
    from shared.types import AioRedis
```

Then replace `ingest_observation` (lines 65-84) with:

```python
async def ingest_observation(
    obs: ReflexObservation,
    episodic_memory: EpisodicMemory,
    scorer: SignificanceScorer,
    passive_scorer: SignificanceScorer | None = None,
) -> None:
    """Convert a ReflexObservation into an episodic entry and store it.

    ``obs.action is None`` means the Reflex Engine saw the event and took no
    action. Those are stored under source ``"observation"`` and scored by
    ``passive_scorer`` (which tracks its own entity-frequency population, so
    passive volume cannot flatten novelty for real reflex actions).
    """
    # Bound to locals so mypy narrows them for the action branch below.
    # An action without a result is unreachable in practice (publish_observation
    # always sets both) — treating it as passive degrades gracefully rather
    # than crashing the ingest loop.
    action, result = obs.action, obs.result
    passive = action is None or result is None

    if action is None or result is None:
        entry = EpisodicEntry(
            id=str(uuid4()),
            timestamp=obs.timestamp,
            source="observation",
            summary=_build_observation_summary(obs),
            entities=_extract_entities(obs),
            significance=SignificanceScore(overall=0.0),  # placeholder, scored below
            semantic_key=_build_observation_semantic_key(obs),
            valence="neutral",
        )
    else:
        entry = EpisodicEntry(
            id=str(uuid4()),
            timestamp=obs.timestamp,
            source="reflex",
            summary=_build_summary(obs, action, result),
            entities=_extract_entities(obs),
            significance=SignificanceScore(overall=0.0),  # placeholder, scored below
            semantic_key=_build_semantic_key(obs, action),
            valence="neutral",
        )

    active_scorer = passive_scorer if (passive and passive_scorer is not None) else scorer
    significance = await active_scorer.score(entry)
    await episodic_memory.write(entry, significance)
    logger.debug("Ingested observation {}: {}", obs.observation_id, entry.summary)
```

Finally, `_extract_entities` (lines 50-62) dereferences `obs.action` unconditionally. Replace its body:

```python
def _extract_entities(obs: ReflexObservation) -> list[str]:
    """Extract entity IDs from the observation."""
    entities: set[str] = set()
    # From action parameters (entity_id is common). Absent on passive
    # observations, which carry only the trigger event.
    if obs.action is not None:
        for key in ("entity_id", "room", "device"):
            val = obs.action.parameters.get(key)
            if val and isinstance(val, str):
                entities.add(val)
    # From trigger_event
    val = obs.trigger_event.get("entity_id")
    if val and isinstance(val, str):
        entities.add(val)
    return sorted(entities)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/core/memory/test_passive_ingestion.py tests/core/memory/test_ingestor.py -v
```

Expected: all pass — the new passive tests and the pre-existing action-path tests.

- [ ] **Step 5: Pass the passive scorer through the consumer loop**

`run_ingestor` builds nothing itself, so it must accept and forward the passive scorer. In `core/memory/ingestor.py`, change the `run_ingestor` signature (lines 87-92):

```python
async def run_ingestor(
    redis: AioRedis,
    episodic_memory: EpisodicMemory,
    scorer: SignificanceScorer,
    shutdown_event: asyncio.Event | None = None,
    passive_scorer: SignificanceScorer | None = None,
) -> None:
```

and the call inside the loop (line 119):

```python
                    await ingest_observation(
                        obs, episodic_memory, scorer, passive_scorer=passive_scorer
                    )
```

- [ ] **Step 6: Run the memory suite**

```bash
uv run pytest tests/core/memory core/memory -q
uv run mypy core/
```

Expected: all tests pass and mypy is clean. If mypy still reports `Item "None" of "ActionRequest | None" has no attribute "parameters"`, a helper is still taking `obs` where it should take the narrowed `action`.

- [ ] **Step 7: Commit**

```bash
git add core/memory/ingestor.py tests/core/memory/test_passive_ingestion.py
git commit -m "feat(memory): ingest passive observations as source=observation"
```

---

## Task 7: Wire the passive scorer at the entry point

Everything above is inert until `ingestor_main.py` builds a scorer pointed at `OBSERVED_FREQUENCY_KEY`. This is the one place a mistake is silent — the ingestor would fall back to the shared scorer and quietly contaminate the novelty population.

**Files:**
- Modify: `core/memory/ingestor_main.py:52, 66`
- Test: `tests/core/memory/test_passive_ingestion.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/core/memory/test_passive_ingestion.py`:

```python
# ---------------------------------------------------------------------------
# Entry-point wiring
# ---------------------------------------------------------------------------


def test_ingestor_main_builds_a_passive_scorer_on_the_observed_key() -> None:
    """Guard the one wiring mistake that would fail silently in production."""
    import inspect

    from core.memory import ingestor_main

    source = inspect.getsource(ingestor_main.run)
    assert "OBSERVED_FREQUENCY_KEY" in source
    assert "passive_scorer" in source
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/core/memory/test_passive_ingestion.py -k ingestor_main -v
```

Expected: FAIL with `AssertionError: assert 'OBSERVED_FREQUENCY_KEY' in ...`.

- [ ] **Step 3: Wire it up**

In `core/memory/ingestor_main.py`, add the import after line 24 (`from shared.redis_streams import create_redis`):

```python
from shared.redis_streams import create_redis
from shared.streams import OBSERVED_FREQUENCY_KEY
```

Replace line 52:

```python
    scorer = SignificanceScorer(redis=r, config=config)
```

with:

```python
    scorer = SignificanceScorer(redis=r, config=config)
    # Passive observations score against their own entity-frequency population.
    # ~250/day on the shared key would drive novelty (1/count) to ~0 for real
    # reflex actions too.
    passive_scorer = SignificanceScorer(
        redis=r, config=config, frequency_key=OBSERVED_FREQUENCY_KEY
    )
```

Replace line 66:

```python
        await run_ingestor(r, episodic, scorer, shutdown_event=_shutdown)
```

with:

```python
        await run_ingestor(
            r, episodic, scorer, shutdown_event=_shutdown, passive_scorer=passive_scorer
        )
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/core/memory/test_passive_ingestion.py -v
```

Expected: all pass.

- [ ] **Step 5: Verify the module imports cleanly**

```bash
uv run python -c "import core.memory.ingestor_main; print('ok')"
```

Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add core/memory/ingestor_main.py tests/core/memory/test_passive_ingestion.py
git commit -m "feat(memory): wire the passive scorer into the ingestor entry point"
```

---

## Task 8: End-to-end check, lint, and PR

**Files:** none modified unless a check fails.

- [ ] **Step 1: Run the full test suite**

```bash
uv run pytest -q
```

Expected: all pass, no errors or warnings introduced by this branch. If `tests/integration/test_reflex_observation_pipeline.py` fails, read it — it hand-builds what `process_stream_entry` produces and may assert the action-path shape. Fix the test only if it encodes the old drop behaviour; otherwise fix the code.

- [ ] **Step 2: Lint and type-check**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/
```

Expected: clean. That target list is copied from `.github/workflows/ci.yml` — match it exactly, or CI will type-check files you never ran locally. Two things to watch for:
- `observe_passively`'s `StateChangedEvent` parameter — the symbol is imported at runtime in `runner.py` (line 14), not under `TYPE_CHECKING`, so the annotation resolves.
- `ruff` may flag the `import os` placement; `ruff format` fixes ordering automatically.

- [ ] **Step 3: Confirm the debounce default is what the spec says**

```bash
uv run python -c "from core.reflex.runner import OBSERVATION_DEBOUNCE_SECONDS as d; assert d == 300, d; print('debounce', d)"
```

Expected: `debounce 300`

- [ ] **Step 4: Push and open the PR**

```bash
git push -u origin feat/passive-observation
gh pr create --title "feat(memory): record what Alfred sees but does not act on" --body "$(cat <<'EOF'
Implements `docs/superpowers/specs/2026-09-03-passive-observation-design.md`.

Alfred has consumed 404,000 `state_changed` events and remembered none of them:
`ReflexObservation` required both `action` and `result`, so "I saw this and chose
not to act" was unrepresentable and the no-action path could only drop the event.
Episodic memory stayed empty, and the Librarian's pattern detection had nothing
to run over.

Three changes, no new service:

- `action` / `result` become optional on `ReflexObservation`
- the Reflex runner publishes a per-entity debounced observation on the
  no-action path (`OBSERVATION_DEBOUNCE_SECONDS`, default 300)
- the Memory Ingestor writes those as `source="observation"` and scores them
  against a separate entity-frequency key, so passive volume cannot flatten
  novelty (`1/count`) for real reflex actions

Expected steady state is ~200-300 entries/day. One entity
(`media_player.macbook_pro`) accounted for 472 of 741 qualifying events over a
29.5-hour measurement window, which is what the debounce is for.

Observations land in the episodic tab of the Memory page. Review point after
~7 days of real data is recorded in the spec and in
`docs/backlog/medium/d33-librarian-insight-summaries.md`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Watch CI**

```bash
gh pr checks --watch
```

Expected: `ci-ok` passes. Note that `ci-ok` does **not** include `container-build` or `voice-smoke` — see `docs/backlog/` on the CI gate gap. Merging deploys to this box via the self-hosted runner.

---

## After merge — verifying on the live instance

Not part of the plan's tasks; do this once deployed.

- [ ] Confirm observations are landing:

```bash
docker exec alfred redis-cli XLEN alfred:reflex:observations
docker exec alfred redis-cli --scan --pattern 'alfred:observer:seen:*' | head
```

- [ ] Confirm episodic entries are being written (was 0 before this change):

```bash
docker exec alfred redis-cli --scan --pattern 'ctx:*' | wc -l
docker exec alfred redis-cli ZCARD alfred:entity:freq:observed
```

- [ ] Confirm the shared frequency key is *not* growing from passive traffic:

```bash
docker exec alfred redis-cli ZCARD alfred:entity:freq
```

- [ ] Open the Memory page and read the episodic tab.

- [ ] After ~7 days, answer the three questions in the spec's "Review point" section. That answer — not a guess made now — decides whether D33 gets built.
