# D8: System 2 Observation of System 1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable the Conscious Engine (System 2) to recall Reflex Engine (System 1) actions via episodic memory, and the Librarian to detect patterns in reflexive behavior.

**Architecture:** Reflex publishes structured `ReflexObservation` events to a dedicated Redis stream. A new Memory Ingestor consumer (hippocampus) in `core/memory/` reads the stream and writes directly to `EpisodicMemory`. The Conscious Engine accesses Reflex actions through normal episodic memory search (involuntary recall). The Librarian's consolidation prompt is enhanced to analyze Reflex-sourced entries for patterns.

**Tech Stack:** Python 3.13+, Pydantic v2, Redis Streams, pytest + pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-04-16-d8-system2-observation-design.md`

---

### Task 1: Add `ReflexObservation` Event Schema

**Files:**
- Modify: `bus/schemas/events.py:129` (append after `TriggerCreated`)
- Test: `bus/tests/test_events.py` (add new test)

- [ ] **Step 1: Write the failing test**

Create test in `bus/tests/test_events.py`:

```python
def test_reflex_observation_schema() -> None:
    """ReflexObservation carries full context of a Reflex action."""
    from bus.schemas.events import ActionRequest, ActionResult, ReflexObservation

    action = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="lighting.dim_lights",
        parameters={"room": "living_room", "level": 20},
    )
    result = ActionResult(
        source="home-service",
        request_id=action.request_id,
        tool_name="lighting.dim_lights",
        status="success",
    )
    obs = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event={"entity_id": "media_player.living_room_tv", "new_state": "on"},
        action=action,
        result=result,
        decision_context="TV turned on in living room, dimming lights per preference",
    )

    assert obs.observation_id  # auto-generated
    assert obs.timestamp  # auto-generated
    assert obs.origin == "state_change"
    assert obs.action.tool_name == "lighting.dim_lights"
    assert obs.result.status == "success"
    assert obs.decision_context is not None

    # Roundtrip serialization
    json_str = obs.model_dump_json()
    restored = ReflexObservation.model_validate_json(json_str)
    assert restored.observation_id == obs.observation_id
    assert restored.action.tool_name == "lighting.dim_lights"


def test_reflex_observation_defaults() -> None:
    """ReflexObservation works with minimal fields."""
    from bus.schemas.events import ActionRequest, ActionResult, ReflexObservation

    action = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="smart_home.turn_on",
        parameters={"entity_id": "light.hallway"},
    )
    result = ActionResult(
        source="home-service",
        request_id=action.request_id,
        tool_name="smart_home.turn_on",
        status="success",
    )
    obs = ReflexObservation(
        source="reflex-engine",
        origin="trigger_fired",
        trigger_event={},
        action=action,
        result=result,
    )

    assert obs.decision_context is None
    assert obs.event_type == "reflex_observation"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest bus/tests/test_events.py::test_reflex_observation_schema bus/tests/test_events.py::test_reflex_observation_defaults -v`
Expected: FAIL with `ImportError: cannot import name 'ReflexObservation'`

- [ ] **Step 3: Implement `ReflexObservation` model**

Add to the end of `bus/schemas/events.py` (after the `TriggerCreated` class):

```python
class ReflexObservation(BaseEvent):
    """A structured observation of a Reflex Engine action for System 2 awareness.

    Published after every Reflex action execution. The Memory Ingestor
    consumes these and writes them to episodic memory so that the
    Conscious Engine can recall Reflex actions during context assembly.
    """

    event_type: str = "reflex_observation"
    observation_id: str = Field(default_factory=lambda: str(uuid4()))
    origin: Literal["state_change", "trigger_fired"]
    trigger_event: dict[str, Any] = Field(
        description="The originating event payload (StateChanged or TriggerFired)"
    )
    action: ActionRequest
    result: ActionResult
    decision_context: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest bus/tests/test_events.py::test_reflex_observation_schema bus/tests/test_events.py::test_reflex_observation_defaults -v`
Expected: PASS

- [ ] **Step 5: Run full bus test suite**

Run: `pytest bus/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add bus/schemas/events.py bus/tests/test_events.py
git commit -m "feat(bus): add ReflexObservation event schema for D8 observation pipeline"
```

---

### Task 2: Add `REFLEX_OBSERVATIONS_STREAM` Constant

**Files:**
- Modify: `shared/streams.py:12` (after `HOME_ACTION_RESULTS_STREAM`)

- [ ] **Step 1: Add the stream constant**

Add after `HOME_ACTION_RESULTS_STREAM` line in `shared/streams.py`:

```python
REFLEX_OBSERVATIONS_STREAM = "alfred:reflex:observations"
```

- [ ] **Step 2: Verify imports work**

Run: `python -c "from shared.streams import REFLEX_OBSERVATIONS_STREAM; print(REFLEX_OBSERVATIONS_STREAM)"`
Expected: `alfred:reflex:observations`

- [ ] **Step 3: Commit**

```bash
git add shared/streams.py
git commit -m "feat(shared): add REFLEX_OBSERVATIONS_STREAM constant"
```

---

### Task 3: Replace Reflex Runner Scratchpad Write with Observation Publish

**Files:**
- Modify: `core/reflex/runner.py:44-88` (`process_stream_entry` function)
- Test: `core/reflex/tests/test_runner.py`

- [ ] **Step 1: Update the existing test to expect observation stream publish**

In `core/reflex/tests/test_runner.py`, update `test_process_stream_entry_produces_action`:

Replace the function signature and assertions. The function now takes `observation_stream` instead of `scratchpad_queue`, and publishes a `ReflexObservation` via `xadd` instead of `lpush`:

```python
@pytest.mark.asyncio
async def test_process_stream_entry_produces_action() -> None:
    """A valid state change event should be processed and produce an action + observation."""
    from core.reflex.runner import process_stream_entry

    event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="media_player.living_room_tv",
        old_state="off",
        new_state="on",
        attributes={"friendly_name": "Living Room TV"},
    )
    event_json = event.model_dump_json()

    mock_engine = AsyncMock()
    mock_engine.process_event.return_value = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="smart_home.dim_lights",
        parameters={"room": "living_room", "level": 20},
    )

    mock_agent = AsyncMock()
    mock_agent.execute_action.return_value = MagicMock(
        model_dump_json=MagicMock(return_value='{"status":"success"}'),
        status="success",
        request_id="r-1",
        tool_name="smart_home.dim_lights",
        source="home-service",
    )

    mock_redis = AsyncMock()

    result = await process_stream_entry(
        entry_data={"event": event_json},
        engine=mock_engine,
        agent=mock_agent,
        redis=mock_redis,
        result_stream="alfred:home:action_results",
        observation_stream="alfred:reflex:observations",
    )

    assert result is True
    mock_engine.process_event.assert_called_once()
    mock_agent.execute_action.assert_called_once()
    # Two xadd calls: one for result stream, one for observation stream
    assert mock_redis.xadd.call_count == 2


@pytest.mark.asyncio
async def test_process_stream_entry_publishes_reflex_observation() -> None:
    """Observation published to stream includes structured ReflexObservation."""
    from bus.schemas.events import ReflexObservation
    from core.reflex.runner import process_stream_entry

    event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="light.hallway",
        old_state="off",
        new_state="on",
    )

    action = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="smart_home.turn_on",
        parameters={"entity_id": "light.hallway"},
    )

    mock_engine = AsyncMock()
    mock_engine.process_event.return_value = action

    mock_agent = AsyncMock()
    mock_agent.execute_action.return_value = MagicMock(
        model_dump_json=MagicMock(return_value='{"status":"success"}'),
        status="success",
        request_id=action.request_id,
        tool_name="smart_home.turn_on",
        source="home-service",
    )

    mock_redis = AsyncMock()

    await process_stream_entry(
        entry_data={"event": event.model_dump_json()},
        engine=mock_engine,
        agent=mock_agent,
        redis=mock_redis,
        result_stream="alfred:home:action_results",
        observation_stream="alfred:reflex:observations",
    )

    # Find the observation xadd call (second call)
    obs_call = mock_redis.xadd.call_args_list[1]
    assert obs_call.args[0] == "alfred:reflex:observations"
    obs_json = obs_call.args[1]["event"]
    obs = ReflexObservation.model_validate_json(obs_json)
    assert obs.origin == "state_change"
    assert obs.action.tool_name == "smart_home.turn_on"
```

- [ ] **Step 2: Update remaining tests for new function signature**

In `test_process_stream_entry_no_action`, `test_process_stream_entry_malformed_event`, and `test_process_stream_entry_handles_bytes_keys`, replace `scratchpad_queue="alfred:scratchpad:queue"` with `observation_stream="alfred:reflex:observations"`.

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest core/reflex/tests/test_runner.py -v`
Expected: FAIL (function signature mismatch)

- [ ] **Step 4: Implement the changes in `core/reflex/runner.py`**

Update `process_stream_entry` to replace the scratchpad write with observation stream publish:

```python
async def process_stream_entry(
    entry_data: Mapping[str | bytes, str | bytes],
    engine: ReflexEngine,
    agent: DomainAgent,
    redis: AioRedis,
    result_stream: str,
    observation_stream: str,
) -> bool:
    """Process a single Redis Stream entry. Returns True if an action was taken.

    Raises on retriable errors (e.g., Ollama down) so the caller can
    choose not to ACK the message. Returns False for skip-worthy errors
    (malformed event, no action needed).
    """
    raw_event = entry_data.get("event") or entry_data.get(b"event")
    if raw_event is None:
        logger.warning("Stream entry missing 'event' field: %s", entry_data)
        return False

    event_str = decode_stream_value(raw_event)

    try:
        event = StateChangedEvent.model_validate_json(event_str)
    except Exception as e:
        logger.error("Failed to parse event: %s — %s", e, event_str[:200])
        return False

    action = await engine.process_event(event)
    if action is None:
        logger.debug("No action for event %s", event.entity_id)
        return False

    result = await agent.execute_action(action)

    await redis.xadd(result_stream, {"event": result.model_dump_json()})

    # Publish structured observation for Memory Ingestor (D8)
    from bus.schemas.events import ActionResult, ReflexObservation

    observation = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event=event.model_dump(),
        action=action,
        result=ActionResult(
            source=str(getattr(result, "source", "unknown")),
            request_id=str(getattr(result, "request_id", action.request_id)),
            tool_name=str(getattr(result, "tool_name", action.tool_name)),
            status=getattr(result, "status", "success"),
        ),
    )
    await redis.xadd(observation_stream, {"event": observation.model_dump_json()})

    logger.info("Action: %s → %s (status=%s)", event.entity_id, action.tool_name, getattr(result, "status", "unknown"))
    return True
```

Also update the imports at the top of `runner.py` — add `ReflexObservation` is imported inside the function to avoid circular deps.

Remove the `SCRATCHPAD_QUEUE` import from `runner.py` if it's no longer used there (it's only used in `__main__.py`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest core/reflex/tests/test_runner.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add core/reflex/runner.py core/reflex/tests/test_runner.py
git commit -m "feat(reflex): publish ReflexObservation to stream instead of scratchpad write

Replaces plain-text scratchpad lpush with structured ReflexObservation
xadd in process_stream_entry (state change path)."
```

---

### Task 4: Update Reflex `__main__.py` — TriggerFired Path + Caller

**Files:**
- Modify: `core/reflex/__main__.py:67-111` (`_handle_trigger_fired`) and `core/reflex/__main__.py:220-245` (main loop caller)
- Test: `core/reflex/tests/test_trigger_fired_consumer.py`

- [ ] **Step 1: Update TriggerFired test to expect observation publish**

In `core/reflex/tests/test_trigger_fired_consumer.py`, update `test_handle_trigger_fired_with_slm_action`:

Replace the final assertion block:

```python
@pytest.mark.asyncio
async def test_handle_trigger_fired_with_slm_action(
    mock_agent: AsyncMock,
    mock_publisher: AsyncMock,
) -> None:
    from bus.schemas.events import ActionRequest, ActionResult
    from core.reflex.__main__ import _handle_trigger_fired

    action_result = ActionResult(
        source="home-service",
        request_id="r-1",
        tool_name="lighting.dim_lights",
        status="success",
    )
    mock_agent.execute_action = AsyncMock(return_value=action_result)

    engine = AsyncMock()
    engine.process_trigger_fired = AsyncMock(
        return_value=ActionRequest(
            source="reflex-engine",
            target_service="home-service",
            tool_name="lighting.dim_lights",
            parameters={"room": "bedroom", "level": 10},
        )
    )

    event = TriggerFired(
        trigger_id="t-1",
        trigger_name="bedtime",
        trigger_type="time",
    )
    redis = AsyncMock()
    redis.xadd = AsyncMock()

    await _handle_trigger_fired(
        _make_entry_data(event),
        engine,
        mock_agent,
        redis,
        mock_publisher,
    )

    mock_publisher.publish.assert_called_once()
    mock_agent.execute_action.assert_called_once()
    # Two xadd calls: result stream + observation stream
    assert redis.xadd.call_count == 2
```

- [ ] **Step 2: Add test for observation content in TriggerFired path**

Add new test to `core/reflex/tests/test_trigger_fired_consumer.py`:

```python
@pytest.mark.asyncio
async def test_handle_trigger_fired_publishes_observation(
    mock_agent: AsyncMock,
    mock_publisher: AsyncMock,
) -> None:
    """TriggerFired path publishes structured ReflexObservation."""
    from bus.schemas.events import ActionRequest, ActionResult, ReflexObservation
    from core.reflex.__main__ import _handle_trigger_fired

    action_result = ActionResult(
        source="home-service",
        request_id="r-1",
        tool_name="lighting.dim_lights",
        status="success",
    )
    mock_agent.execute_action = AsyncMock(return_value=action_result)

    engine = AsyncMock()
    engine.process_trigger_fired = AsyncMock(
        return_value=ActionRequest(
            source="reflex-engine",
            target_service="home-service",
            tool_name="lighting.dim_lights",
            parameters={"room": "bedroom", "level": 10},
        )
    )

    event = TriggerFired(
        trigger_id="t-1",
        trigger_name="bedtime dim",
        trigger_type="time",
    )
    redis = AsyncMock()
    redis.xadd = AsyncMock()

    await _handle_trigger_fired(
        _make_entry_data(event),
        engine,
        mock_agent,
        redis,
        mock_publisher,
    )

    # Second xadd is the observation
    obs_call = redis.xadd.call_args_list[1]
    obs_json = obs_call.args[1]["event"]
    obs = ReflexObservation.model_validate_json(obs_json)
    assert obs.origin == "trigger_fired"
    assert obs.action.tool_name == "lighting.dim_lights"
    assert obs.result.status == "success"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest core/reflex/tests/test_trigger_fired_consumer.py -v`
Expected: FAIL (assertion count mismatch and new test fails)

- [ ] **Step 4: Update `_handle_trigger_fired` in `core/reflex/__main__.py`**

Replace the scratchpad write (lines 103-108) with observation stream publish:

```python
    # Path B: Reflex SLM reasoning (isolated — failures don't block ACK)
    try:
        action = await engine.process_trigger_fired(trigger_event)
        if action is not None:
            result = await agent.execute_action(action)
            await redis.xadd(HOME_ACTION_RESULTS_STREAM, {"event": result.model_dump_json()})

            # Publish structured observation (D8)
            from bus.schemas.events import ReflexObservation

            observation = ReflexObservation(
                source="reflex-engine",
                origin="trigger_fired",
                trigger_event=trigger_event.model_dump(),
                action=action,
                result=result,
            )
            await redis.xadd(
                REFLEX_OBSERVATIONS_STREAM,
                {"event": observation.model_dump_json()},
            )
    except Exception as e:
        logger.error("SLM reasoning failed for trigger '%s': %s", trigger_event.trigger_name, e)
```

Also update `__main__.py` imports: add `REFLEX_OBSERVATIONS_STREAM` to the `shared.streams` import block, remove `SCRATCHPAD_QUEUE` if it's no longer used (check if `ScratchpadWriter` still needs it — it does, the writer is started in `run()` for the Conscious Engine's scratchpad writes which still happen via Conscious Engine).

Wait — `SCRATCHPAD_QUEUE` is still used in `__main__.py` for the `ScratchpadWriter` construction on line 206. Keep the import. Only remove the `lpush` call in `_handle_trigger_fired`.

Update the main loop caller (around line 232-239) to pass `observation_stream` instead of `scratchpad_queue`:

```python
                    await process_stream_entry(
                        entry_data=entry_data,
                        engine=engine,
                        agent=router,
                        redis=r,
                        result_stream=RESULT_STREAM,
                        observation_stream=REFLEX_OBSERVATIONS_STREAM,
                    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest core/reflex/tests/test_trigger_fired_consumer.py core/reflex/tests/test_runner.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add core/reflex/__main__.py core/reflex/tests/test_trigger_fired_consumer.py
git commit -m "feat(reflex): publish ReflexObservation from TriggerFired path

Replaces scratchpad lpush with structured observation xadd in both
_handle_trigger_fired and the main event loop caller."
```

---

### Task 5: Create Memory Ingestor

**Files:**
- Create: `core/memory/ingestor.py`
- Test: `core/memory/tests/test_ingestor.py`

- [ ] **Step 1: Write the failing tests**

Create `core/memory/tests/test_ingestor.py`:

```python
"""Tests for the Memory Ingestor — writes ReflexObservations to episodic memory."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bus.schemas.events import ActionRequest, ActionResult, ReflexObservation
from core.memory.schemas import EpisodicEntry


def _make_observation(
    tool_name: str = "smart_home.turn_on",
    entity_id: str = "light.hallway",
    status: str = "success",
    origin: str = "state_change",
    decision_context: str | None = None,
) -> ReflexObservation:
    action = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name=tool_name,
        parameters={"entity_id": entity_id},
    )
    result = ActionResult(
        source="home-service",
        request_id=action.request_id,
        tool_name=tool_name,
        status=status,
    )
    return ReflexObservation(
        source="reflex-engine",
        origin=origin,
        trigger_event={"entity_id": entity_id, "new_state": "on"},
        action=action,
        result=result,
        decision_context=decision_context,
    )


@pytest.mark.asyncio
async def test_ingest_observation_writes_to_episodic() -> None:
    """Memory Ingestor stores ReflexObservation as episodic entry."""
    from core.memory.ingestor import ingest_observation

    mock_episodic = AsyncMock()
    mock_scorer = AsyncMock()
    mock_scorer.score = AsyncMock(
        return_value=MagicMock(overall=0.3, safety=0.0, novelty=0.2, personal=0.1, emotional=0.0)
    )

    obs = _make_observation()

    await ingest_observation(obs, mock_episodic, mock_scorer)

    mock_episodic.write.assert_called_once()
    entry: EpisodicEntry = mock_episodic.write.call_args.args[0]
    assert entry.source == "reflex"
    assert "smart_home.turn_on" in entry.summary
    assert "light.hallway" in entry.summary
    assert entry.entities == ["light.hallway"]
    mock_scorer.score.assert_called_once()


@pytest.mark.asyncio
async def test_ingest_observation_includes_decision_context() -> None:
    """Decision context from SLM reasoning is included in summary."""
    from core.memory.ingestor import ingest_observation

    mock_episodic = AsyncMock()
    mock_scorer = AsyncMock()
    mock_scorer.score = AsyncMock(
        return_value=MagicMock(overall=0.5, safety=0.0, novelty=0.5, personal=0.0, emotional=0.0)
    )

    obs = _make_observation(decision_context="Motion detected at night, turning on hallway light")

    await ingest_observation(obs, mock_episodic, mock_scorer)

    entry: EpisodicEntry = mock_episodic.write.call_args.args[0]
    assert "Motion detected at night" in entry.summary


@pytest.mark.asyncio
async def test_ingest_observation_trigger_fired_origin() -> None:
    """TriggerFired origin is reflected in the episodic entry."""
    from core.memory.ingestor import ingest_observation

    mock_episodic = AsyncMock()
    mock_scorer = AsyncMock()
    mock_scorer.score = AsyncMock(
        return_value=MagicMock(overall=0.3, safety=0.0, novelty=0.2, personal=0.1, emotional=0.0)
    )

    obs = _make_observation(origin="trigger_fired")

    await ingest_observation(obs, mock_episodic, mock_scorer)

    entry: EpisodicEntry = mock_episodic.write.call_args.args[0]
    assert entry.source == "reflex"
    assert "trigger" in entry.semantic_key.lower() or "reflex" in entry.semantic_key.lower()


@pytest.mark.asyncio
async def test_ingest_observation_extracts_entities() -> None:
    """Entities are extracted from trigger_event and action parameters."""
    from core.memory.ingestor import ingest_observation

    mock_episodic = AsyncMock()
    mock_scorer = AsyncMock()
    mock_scorer.score = AsyncMock(
        return_value=MagicMock(overall=0.3, safety=0.0, novelty=0.2, personal=0.1, emotional=0.0)
    )

    obs = _make_observation(entity_id="light.kitchen")

    await ingest_observation(obs, mock_episodic, mock_scorer)

    entry: EpisodicEntry = mock_episodic.write.call_args.args[0]
    assert "light.kitchen" in entry.entities
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest core/memory/tests/test_ingestor.py -v`
Expected: FAIL with `ImportError: cannot import name 'ingest_observation'`

- [ ] **Step 3: Implement `core/memory/ingestor.py`**

```python
"""Memory Ingestor — the hippocampus.

Consumes ReflexObservation events from the observation stream and
writes them directly to episodic memory. This is the bridge between
System 1 actions and System 2 awareness.

Runs as a background task in the unified runner.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from bus.schemas.events import ReflexObservation
from core.memory.schemas import EpisodicEntry, SignificanceScore
from shared.streams import REFLEX_OBSERVATIONS_STREAM, decode_stream_value

if TYPE_CHECKING:
    from core.memory.episodic.memory import EpisodicMemory
    from core.memory.significance import SignificanceScorer
    from shared.types import AioRedis

logger = logging.getLogger(__name__)

GROUP = "memory-ingestor"
CONSUMER = "worker-1"


def _build_summary(obs: ReflexObservation) -> str:
    """Build a human-readable summary for embedding."""
    params_str = ", ".join(f"{k}={v}" for k, v in obs.action.parameters.items())
    base = (
        f"[reflex:{obs.origin}] {obs.action.tool_name}({params_str}) "
        f"→ {obs.result.status}"
    )
    if obs.decision_context:
        base += f" | reason: {obs.decision_context}"
    return base


def _build_semantic_key(obs: ReflexObservation) -> str:
    """Build a semantic key optimised for vector search."""
    return (
        f"Reflex {obs.origin} action: {obs.action.tool_name} "
        f"on {', '.join(obs.action.parameters.values()) if obs.action.parameters else 'unknown'}"
    )


def _extract_entities(obs: ReflexObservation) -> list[str]:
    """Extract entity IDs from the observation."""
    entities: set[str] = set()
    # From action parameters (entity_id is common)
    for key in ("entity_id", "room", "device"):
        val = obs.action.parameters.get(key)
        if val and isinstance(val, str):
            entities.add(val)
    # From trigger_event
    for key in ("entity_id",):
        val = obs.trigger_event.get(key)
        if val and isinstance(val, str):
            entities.add(val)
    return sorted(entities)


async def ingest_observation(
    obs: ReflexObservation,
    episodic_memory: EpisodicMemory,
    scorer: SignificanceScorer,
) -> None:
    """Convert a ReflexObservation into an episodic entry and store it."""
    entry = EpisodicEntry(
        id=str(uuid4()),
        timestamp=obs.timestamp,
        source="reflex",
        summary=_build_summary(obs),
        entities=_extract_entities(obs),
        significance=SignificanceScore(overall=0.0),  # placeholder, scored below
        semantic_key=_build_semantic_key(obs),
        valence="neutral",
    )

    significance = await scorer.score(entry)
    await episodic_memory.write(entry, significance)
    logger.debug("Ingested reflex observation %s: %s", obs.observation_id, entry.summary)


async def run_ingestor(
    redis: AioRedis,
    episodic_memory: EpisodicMemory,
    scorer: SignificanceScorer,
    shutdown_event: "asyncio.Event | None" = None,
) -> None:
    """Consumer loop — reads REFLEX_OBSERVATIONS_STREAM, writes to episodic memory."""
    import asyncio

    from core.reflex.runner import ensure_consumer_group

    await ensure_consumer_group(redis, REFLEX_OBSERVATIONS_STREAM, GROUP)
    logger.info("Memory Ingestor started. Consuming '%s'...", REFLEX_OBSERVATIONS_STREAM)

    while True:
        if shutdown_event and shutdown_event.is_set():
            break

        entries: list[
            tuple[
                bytes | str,
                list[tuple[bytes | str, dict[bytes | str, bytes | str]]],
            ]
        ] = await redis.xreadgroup(
            GROUP,
            CONSUMER,
            {REFLEX_OBSERVATIONS_STREAM: ">"},
            count=10,
            block=5000,
        )

        for _stream_key, stream_entries in entries:
            for entry_id, entry_data in stream_entries:
                try:
                    raw = entry_data.get("event") or entry_data.get(b"event")
                    if raw is None:
                        await redis.xack(REFLEX_OBSERVATIONS_STREAM, GROUP, entry_id)
                        continue

                    event_str = decode_stream_value(raw)
                    obs = ReflexObservation.model_validate_json(event_str)
                    await ingest_observation(obs, episodic_memory, scorer)
                    await redis.xack(REFLEX_OBSERVATIONS_STREAM, GROUP, entry_id)
                except Exception as e:
                    logger.error(
                        "Error ingesting observation %s: %s — will retry",
                        entry_id,
                        e,
                    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest core/memory/tests/test_ingestor.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add core/memory/ingestor.py core/memory/tests/test_ingestor.py
git commit -m "feat(memory): add Memory Ingestor for ReflexObservation → episodic pipeline

Hippocampus consumer: reads REFLEX_OBSERVATIONS_STREAM, builds episodic
entries with structured metadata, writes to EpisodicMemory via
SignificanceScorer."
```

---

### Task 6: Wire Memory Ingestor into Runner

**Files:**
- Modify: `runner/__main__.py:22-38` (SERVICES list)

The Memory Ingestor is not a standalone process — it's a lightweight consumer loop. However, looking at how the runner works (it spawns each service as a child process via `python -m <module>`), the Ingestor needs its own `__main__.py` entry point.

- [ ] **Step 1: Create `core/memory/ingestor_main.py`**

Create `core/memory/ingestor_main.py` (separate from `ingestor.py` to keep the consumer logic testable):

```python
"""Entry point for the Memory Ingestor service.

Usage: python -m core.memory.ingestor_main

Lightweight consumer that bridges Reflex observations into episodic memory.
"""

from __future__ import annotations

import asyncio
import logging
import signal

import redis.asyncio as aioredis

from core.memory.episodic.memory import EpisodicMemory
from core.memory.ingestor import run_ingestor
from core.memory.redis_vector_store import RedisVectorStore
from core.memory.significance import SignificanceScorer
from core.memory.sqlite_vec_store import SqliteVecStore
from shared.config import AlfredConfig
from shared.logging import configure_logging
from shared.streams import CONTEXT_INDEX, CONTEXT_PREFIX

logger = logging.getLogger(__name__)

_shutdown = asyncio.Event()


def _handle_signal() -> None:
    logger.info("Memory Ingestor shutdown signal received")
    _shutdown.set()


async def run(config: AlfredConfig) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    r = aioredis.from_url(config.redis_url)

    # Lazy-load embedding provider
    from core.memory.embedding_provider import SentenceTransformerProvider

    embedder = SentenceTransformerProvider()

    hot = RedisVectorStore(redis=r, index_name=CONTEXT_INDEX, prefix=CONTEXT_PREFIX)
    cold = SqliteVecStore(db_path=str(config.sqlite_vec_path))
    episodic = EpisodicMemory(hot=hot, cold=cold, embedder=embedder)
    scorer = SignificanceScorer(redis=r, config=config)

    try:
        await run_ingestor(r, episodic, scorer, shutdown_event=_shutdown)
    finally:
        await r.aclose()


def main() -> None:
    configure_logging(service="memory-ingestor")
    config = AlfredConfig.from_env()
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Add to runner SERVICES list**

In `runner/__main__.py`, add the Memory Ingestor as a new service. It should start after the reflex engine (so observations have a consumer ready). The module path will need to use a dotted path that resolves to a `__main__.py` or a module with a `main()` function.

Since the runner uses `python -m <module>`, we need the ingestor to be invokable as `python -m core.memory.ingestor_main`. But the supervisor expects a module with a `main()` function. Looking at the runner, it spawns `python -m <module>` as a subprocess. So the ingestor needs to work when invoked as `python -m core.memory.ingestor_main`.

Actually, looking more carefully at the supervisor, it spawns `sys.executable -m {spec.module}`. So we need a module that works with `-m`. The simplest approach: make `ingestor_main.py` runnable directly.

But the module path needs to match. Let's check — `core.memory.ingestor_main` would need `core/memory/ingestor_main.py` to have an `if __name__ == "__main__"` block (already included above).

Add to `runner/__main__.py` SERVICES list:

```python
SERVICES = [
    ServiceSpec(name="bridge", module="bus"),
    ServiceSpec(name="reflex", module="core.reflex", delay=1.0),
    ServiceSpec(name="triggers", module="core.triggers"),
    ServiceSpec(
        name="conscious",
        module="core.conscious",
        delay=2.0,
        watch_dirs=["core/conscious/prompts"],
    ),
    ServiceSpec(
        name="channels",
        module="core.channels",
        delay=2.0,
        watch_dirs=["core/voice", "core/conscious/prompts"],
    ),
    ServiceSpec(name="memory-ingestor", module="core.memory.ingestor_main", delay=1.5),
]
```

- [ ] **Step 3: Verify it imports cleanly**

Run: `python -c "from core.memory.ingestor import run_ingestor; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add core/memory/ingestor_main.py runner/__main__.py
git commit -m "feat(runner): wire Memory Ingestor into unified runner

Adds memory-ingestor as a supervised service (1.5s delay). Starts after
reflex so observations have a consumer ready."
```

---

### Task 7: Enhance Librarian Pattern Detection for Reflex Actions

**Files:**
- Modify: `core/librarian/consolidator.py:511-611` (`_detect_patterns` method)
- Test: `core/librarian/tests/test_consolidator.py` (or appropriate test file)

- [ ] **Step 1: Find existing Librarian tests**

Check for existing test files:

Run: `find core/librarian -name "test_*.py" -o -name "*_test.py" | head -10`

- [ ] **Step 2: Write the failing test**

Add test (in the appropriate test file, e.g. `core/librarian/tests/test_consolidator.py`):

```python
@pytest.mark.asyncio
async def test_detect_patterns_includes_reflex_analysis_prompt() -> None:
    """Pattern detection prompt asks LLM to specifically analyze reflex-sourced entries."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from core.librarian.consolidator import Librarian
    from core.memory.schemas import EpisodicEntry, SignificanceScore

    # Create entries — mix of reflex and conscious sources
    now = datetime.now(UTC)
    entries = [
        EpisodicEntry(
            id=f"ep-{i}",
            timestamp=now - timedelta(days=i),
            source="reflex",
            summary=f"[reflex:state_change] smart_home.turn_on(entity_id=light.kitchen) → success",
            entities=["light.kitchen"],
            significance=SignificanceScore(overall=0.3),
            semantic_key="Reflex action: turn on kitchen light",
        )
        for i in range(5)
    ] + [
        EpisodicEntry(
            id="ep-conv-1",
            timestamp=now,
            source="conversation",
            summary="User asked about weather",
            entities=[],
            significance=SignificanceScore(overall=0.5),
        ),
    ]

    mock_redis = AsyncMock()
    mock_episodic = AsyncMock()
    mock_routines = MagicMock()
    mock_routines.list_all.return_value = []
    mock_scorer = AsyncMock()
    mock_context_index = AsyncMock()

    librarian = Librarian(
        redis=mock_redis,
        episodic_memory=mock_episodic,
        routine_store=mock_routines,
        significance_scorer=mock_scorer,
        context_index=mock_context_index,
        claude_api_key="test-key",
    )

    captured_prompt = {}

    async def mock_completion(**kwargs: Any) -> Any:
        captured_prompt["system"] = kwargs["messages"][0]["content"]
        captured_prompt["user"] = kwargs["messages"][1]["content"]
        result = MagicMock()
        result.choices = [MagicMock()]
        result.choices[0].message.content = "[]"
        return result

    with patch("litellm.acompletion", side_effect=mock_completion):
        await librarian._detect_patterns(entries)

    # The prompt should specifically mention reflex/System 1 pattern analysis
    assert "reflex" in captured_prompt["system"].lower() or "system 1" in captured_prompt["system"].lower()
```

- [ ] **Step 3: Run test to verify it fails**

Run the test — it should fail because the current prompt doesn't mention reflex analysis.

- [ ] **Step 4: Update `_detect_patterns` prompt in `core/librarian/consolidator.py`**

Update the `system_prompt` in the `_detect_patterns` method (around line 542-558) to include Reflex-specific analysis:

```python
        system_prompt = (
            "You are a pattern analyst for a home automation assistant. "
            "Given episodic memory entries, identify repeated behavioural patterns. "
            f"Only report patterns with at least {self._pattern_min_occurrences} occurrences "
            f"spread over at least {self._pattern_min_days} different days. "
            "\n\n"
            "PAY SPECIAL ATTENTION to entries with source 'reflex' — these are automatic "
            "System 1 (Reflex Engine) actions taken without conscious reasoning. Look for:\n"
            "- Repeated reflex actions that may be unnecessary or counterproductive "
            "(e.g., lights turning on for pet motion at night)\n"
            "- Reflex patterns that could be optimised or overridden by a learned routine\n"
            "- Reflex actions that are consistent and beneficial (validate as good behaviour)\n\n"
            "Return a JSON array of pattern objects. Each object must have:\n"
            '  "name": short snake_case identifier (e.g. "evening_dim")\n'
            '  "trigger_pattern": when it happens (e.g. "20:00 daily", "weekday morning")\n'
            '  "steps": array of {"description": str} objects\n'
            '  "confidence": float 0.0-1.0\n'
            '  "learned_from": array of episodic entry IDs (from the [id] prefix)\n\n'
            "Return ONLY the JSON array. If no patterns qualify, return [].\n"
            "Example:\n"
            '[\n  {"name": "evening_dim", "trigger_pattern": "20:00 daily",\n'
            '   "steps": [{"description": "Dim living room lights to 30%"}],\n'
            '   "confidence": 0.75, "learned_from": ["ep-1", "ep-3", "ep-7"]}\n]'
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run the test again.
Expected: PASS

- [ ] **Step 6: Run full Librarian test suite**

Run: `pytest core/librarian/ -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add core/librarian/consolidator.py core/librarian/tests/test_consolidator.py
git commit -m "feat(librarian): enhance pattern detection to analyse Reflex actions

Adds reflex-specific instructions to the pattern detection prompt so
the Librarian can identify repeated System 1 patterns, flag unnecessary
actions, and promote beneficial patterns to procedural memory."
```

---

### Task 8: Create Reflex Input Generalization Backlog Ticket

**Files:**
- Create: `docs/backlog/medium/reflex-input-generalization.md`
- Modify: `docs/backlog/medium/d8-system2-observation-system1.md` (update status)

- [ ] **Step 1: Create the backlog ticket**

Create `docs/backlog/medium/reflex-input-generalization.md`:

```markdown
# Reflex Input Generalization

## Summary
The Reflex Engine is currently hardwired to `HOME_STATE_STREAM` — it only handles home automation events. For a general ambient system, Reflex should accept state changes from any domain through a unified or pluggable input mechanism.

## Context
Identified during D8 (System 2 Observation) design. The observation pipeline (D8) is downstream of action execution, so it naturally carries over when inputs are generalized. This ticket is about the input side.

## Acceptance Criteria
- Reflex Engine can consume events from multiple domain streams (not just home)
- New domains can register their state streams without modifying Reflex code
- Existing home automation flow continues working unchanged
- Domain-specific event parsing is pluggable (each domain may have different event shapes)
```

- [ ] **Step 2: Update D8 backlog ticket**

Update `docs/backlog/medium/d8-system2-observation-system1.md` to mark as implemented:

```markdown
# D8: System 2 Observation of System 1 Actions

## Status: IMPLEMENTED

See spec: `docs/superpowers/specs/2026-04-16-d8-system2-observation-design.md`
See plan: `docs/superpowers/plans/2026-04-16-d8-system2-observation-plan.md`

## Summary
Observation pipeline from Reflex Engine → Memory Ingestor → Episodic Memory.
Conscious Engine recalls Reflex actions via episodic search. Librarian detects
patterns in Reflex behaviour during consolidation.
```

- [ ] **Step 3: Commit**

```bash
git add docs/backlog/medium/reflex-input-generalization.md docs/backlog/medium/d8-system2-observation-system1.md
git commit -m "docs: add reflex input generalization backlog ticket, update D8 status"
```

---

### Task 9: Integration Test — End-to-End Observation Pipeline

**Files:**
- Create: `tests/integration/test_reflex_observation_pipeline.py`

- [ ] **Step 1: Write the integration test**

```python
"""Integration test: Reflex action → observation stream → Memory Ingestor → episodic recall."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from bus.schemas.events import ActionRequest, ActionResult, ReflexObservation, StateChangedEvent
from core.memory.ingestor import ingest_observation
from core.memory.schemas import EpisodicEntry, SignificanceScore


@pytest.mark.asyncio
async def test_reflex_observation_reaches_episodic_memory() -> None:
    """Full pipeline: build observation → ingest → verify episodic entry."""
    # Simulate what process_stream_entry builds
    event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="binary_sensor.hallway_motion",
        old_state="off",
        new_state="on",
    )
    action = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="smart_home.turn_on",
        parameters={"entity_id": "light.hallway"},
    )
    result = ActionResult(
        source="home-service",
        request_id=action.request_id,
        tool_name="smart_home.turn_on",
        status="success",
    )
    obs = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event=event.model_dump(),
        action=action,
        result=result,
        decision_context="Motion in hallway at night, turning on light",
    )

    # Mock episodic memory to capture the write
    mock_episodic = AsyncMock()
    mock_scorer = AsyncMock()
    mock_scorer.score = AsyncMock(
        return_value=SignificanceScore(
            overall=0.4, safety=0.1, novelty=0.3, personal=0.0, emotional=0.0
        )
    )

    await ingest_observation(obs, mock_episodic, mock_scorer)

    # Verify episodic write
    mock_episodic.write.assert_called_once()
    entry: EpisodicEntry = mock_episodic.write.call_args.args[0]
    significance: SignificanceScore = mock_episodic.write.call_args.args[1]

    assert entry.source == "reflex"
    assert "smart_home.turn_on" in entry.summary
    assert "light.hallway" in entry.entities
    assert "Motion in hallway" in entry.summary
    assert significance.overall == 0.4


@pytest.mark.asyncio
async def test_observation_roundtrip_serialization() -> None:
    """ReflexObservation survives JSON roundtrip (as it would through Redis stream)."""
    action = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="smart_home.dim_lights",
        parameters={"room": "living_room", "level": 20},
    )
    result = ActionResult(
        source="home-service",
        request_id=action.request_id,
        tool_name="smart_home.dim_lights",
        status="success",
    )
    obs = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event={"entity_id": "media_player.tv", "new_state": "on"},
        action=action,
        result=result,
    )

    # Simulate Redis stream roundtrip
    json_str = obs.model_dump_json()
    restored = ReflexObservation.model_validate_json(json_str)

    assert restored.observation_id == obs.observation_id
    assert restored.action.tool_name == obs.action.tool_name
    assert restored.result.status == obs.result.status
    assert restored.origin == obs.origin
```

- [ ] **Step 2: Run integration test**

Run: `pytest tests/integration/test_reflex_observation_pipeline.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_reflex_observation_pipeline.py
git commit -m "test: add integration test for reflex observation → episodic pipeline"
```

---

### Task 10: Lint, Type Check, and Full Test Suite

**Files:** All modified files

- [ ] **Step 1: Run ruff check**

Run: `ruff check . --fix`
Expected: No errors (or only auto-fixed)

- [ ] **Step 2: Run ruff format**

Run: `ruff format .`
Expected: Clean formatting

- [ ] **Step 3: Run mypy strict**

Run: `mypy --strict bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/`
Expected: No errors

Fix any type errors that arise.

- [ ] **Step 4: Run full test suite**

Run: `pytest -x -q`
Expected: All tests PASS

- [ ] **Step 5: Commit any fixes**

```bash
git add -A
git commit -m "chore: fix lint and type errors from D8 changes"
```

---

### Task 11: Code Architect Review

Run the `feature-dev:code-architect` review agent against the D8 changes. Fix every issue that comes up.

- [ ] **Step 1: Run code architect review**
- [ ] **Step 2: Address all issues found**
- [ ] **Step 3: Commit fixes**

---

### Task 12: Simplify

Run `/simplify` skill on all D8 changes. Fix every issue that comes up.

- [ ] **Step 1: Run simplify review**
- [ ] **Step 2: Address all issues found**
- [ ] **Step 3: Commit fixes**

---

### Task 13: Update CLAUDE.md and Memory

Run `/claude-md-management:claude-md-improver` to audit and update CLAUDE.md files with D8 changes.

- [ ] **Step 1: Run CLAUDE.md improver**
- [ ] **Step 2: Update memory files with D8 completion status**
- [ ] **Step 3: Commit updates**

---

### Task 14: QA Backlog

Run a sub-agent to generate QA backlog items for D8 features that need manual testing.

- [ ] **Step 1: Generate QA backlog items**
- [ ] **Step 2: Review and commit**

---

### Task 15: Create Pull Request

- [ ] **Step 1: Create PR with D8 changes**

```bash
gh pr create --title "D8: System 2 Observation of System 1 Actions" --body "$(cat <<'EOF'
## Summary
- Adds `ReflexObservation` event schema for structured Reflex action reporting
- New Memory Ingestor (hippocampus) consumer writes observations to episodic memory
- Reflex Engine publishes to observation stream instead of plain-text scratchpad
- Librarian pattern detection enhanced to analyze Reflex-sourced entries
- Conscious Engine recalls Reflex actions via existing episodic memory search (no changes needed)

## Architecture
```
Reflex → REFLEX_OBSERVATIONS_STREAM → Memory Ingestor → EpisodicMemory
                                                              ↓
                                          Conscious Engine reads via episodic search
                                          Librarian detects patterns during consolidation
```

## Test plan
- [ ] Unit tests for ReflexObservation schema
- [ ] Unit tests for Memory Ingestor
- [ ] Unit tests for updated Reflex Runner (observation publish)
- [ ] Integration test for full observation pipeline
- [ ] Verify existing tests still pass (no regressions)
- [ ] Manual: run system, trigger a home event, verify observation appears in episodic memory

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
