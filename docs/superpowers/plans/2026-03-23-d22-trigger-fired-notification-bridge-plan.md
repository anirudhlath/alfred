# D22: TriggerFired Notification Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route `TriggerFired` events (actionless triggers) to both a DND-aware notification and the Reflex SLM for additional reasoning.

**Architecture:** Add `urgency` field to `BaseTrigger` and `TriggerFired`. The Reflex process gets a second event loop on `alfred:events` that, for each `TriggerFired`: (1) publishes an immediate DND-aware notification, (2) feeds the event to the SLM for optional additional actions. Error isolation ensures SLM failures don't block notification delivery or cause duplicates on retry.

**Tech Stack:** Python 3.13, Pydantic v2, Redis Streams, Ollama, pytest + pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-03-23-d22-trigger-fired-notification-bridge-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `bus/schemas/events.py:77-85` | Modify | Add `urgency: str` field to `TriggerFired` |
| `core/triggers/models.py:34-55` | Modify | Add `urgency: Urgency` field to `BaseTrigger` |
| `core/triggers/engine.py:41-49` | Modify | Propagate `trigger.urgency.value` into `TriggerFired` |
| `core/triggers/feature.py:48-71,73-123,131-164` | Modify | Add `urgency` param to CRUD, update dynamic description |
| `core/reflex/engine.py` | Modify | Add `_TRIGGER_FIRED_PROMPT_TEMPLATE`, `process_trigger_fired()`, `_parse_slm_json()`, `_build_notification_body()` |
| `core/reflex/__main__.py` | Modify | Add second event loop, notification wiring |
| `bus/schemas/tests/test_events.py` | Modify | Add `TriggerFired.urgency` tests |
| `core/triggers/tests/test_engine.py` | Modify | Add urgency propagation test |
| `core/triggers/tests/test_feature.py` | Modify | Add urgency CRUD tests |
| `core/reflex/tests/test_engine.py` | Modify | Add `process_trigger_fired` + notification body tests |
| `core/reflex/tests/test_trigger_fired_consumer.py` | Create | Integration test for second event loop |

---

### Task 1: Add `urgency` field to `TriggerFired` event schema

**Files:**
- Modify: `bus/schemas/events.py:77-85`
- Modify: `bus/schemas/tests/test_events.py:141-153`

- [ ] **Step 1: Write the failing test**

Add to `bus/schemas/tests/test_events.py`:

```python
def test_trigger_fired_urgency_default() -> None:
    from bus.schemas.events import TriggerFired

    evt = TriggerFired(
        trigger_id="t-1",
        trigger_name="test trigger",
        trigger_type="time",
    )
    assert evt.urgency == "informational"


def test_trigger_fired_urgency_custom() -> None:
    from bus.schemas.events import TriggerFired

    evt = TriggerFired(
        trigger_id="t-1",
        trigger_name="test trigger",
        trigger_type="time",
        urgency="urgent",
    )
    assert evt.urgency == "urgent"


def test_trigger_fired_urgency_roundtrip() -> None:
    from bus.schemas.events import TriggerFired

    evt = TriggerFired(
        trigger_id="t-1",
        trigger_name="test trigger",
        trigger_type="time",
        urgency="important",
    )
    json_str = evt.model_dump_json()
    restored = TriggerFired.model_validate_json(json_str)
    assert restored.urgency == "important"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && .venv/bin/python -m pytest bus/schemas/tests/test_events.py::test_trigger_fired_urgency_default bus/schemas/tests/test_events.py::test_trigger_fired_urgency_custom bus/schemas/tests/test_events.py::test_trigger_fired_urgency_roundtrip -v`
Expected: FAIL — `TriggerFired() got an unexpected keyword argument 'urgency'` or missing attribute

- [ ] **Step 3: Add `urgency` field to `TriggerFired`**

In `bus/schemas/events.py`, add after line 85 (`context` field):

```python
    urgency: str = "informational"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && .venv/bin/python -m pytest bus/schemas/tests/test_events.py -v`
Expected: ALL PASS (including existing `test_trigger_fired_defaults`)

- [ ] **Step 5: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
git add bus/schemas/events.py bus/schemas/tests/test_events.py
git commit -m "feat(bus): add urgency field to TriggerFired event schema"
```

---

### Task 2: Add `urgency` field to `BaseTrigger` model

**Files:**
- Modify: `core/triggers/models.py:34-55`
- Modify: `core/triggers/tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Add to `core/triggers/tests/test_models.py`:

```python
def test_base_trigger_urgency_default() -> None:
    from core.triggers.registry import TriggerRegistry
    import core.triggers.types  # noqa: F401

    cls = TriggerRegistry.get("time")
    trigger = cls(
        trigger_id="t-1",
        trigger_type="time",
        name="test",
        created_by="test",
        created_at=datetime.now(UTC),
        conditions={"cron": "0 7 * * *"},
    )
    from core.notifications.schema import Urgency
    assert trigger.urgency == Urgency.INFORMATIONAL


def test_base_trigger_urgency_custom() -> None:
    from core.triggers.registry import TriggerRegistry
    import core.triggers.types  # noqa: F401

    from core.notifications.schema import Urgency

    cls = TriggerRegistry.get("time")
    trigger = cls(
        trigger_id="t-1",
        trigger_type="time",
        name="test",
        created_by="test",
        created_at=datetime.now(UTC),
        conditions={"cron": "0 7 * * *"},
        urgency=Urgency.URGENT,
    )
    assert trigger.urgency == Urgency.URGENT
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && .venv/bin/python -m pytest core/triggers/tests/test_models.py::test_base_trigger_urgency_default core/triggers/tests/test_models.py::test_base_trigger_urgency_custom -v`
Expected: FAIL — unexpected keyword argument or missing attribute

- [ ] **Step 3: Add `urgency` field to `BaseTrigger`**

In `core/triggers/models.py`, add import and field:

```python
# Add to imports
from core.notifications.schema import Urgency

class BaseTrigger(ABC, BaseModel):
    ...
    action: ActionPayload | None = None
    urgency: Urgency = Urgency.INFORMATIONAL  # after action field
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && .venv/bin/python -m pytest core/triggers/tests/test_models.py -v`
Expected: ALL PASS

- [ ] **Step 5: Run full trigger test suite to check nothing broke**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && .venv/bin/python -m pytest core/triggers/tests/ -v`
Expected: ALL PASS (existing tests unaffected — `urgency` has a default)

- [ ] **Step 6: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
git add core/triggers/models.py core/triggers/tests/test_models.py
git commit -m "feat(triggers): add urgency field to BaseTrigger model"
```

---

### Task 3: Propagate urgency in `TriggerEngine.fire()`

**Files:**
- Modify: `core/triggers/engine.py:41-49`
- Modify: `core/triggers/tests/test_engine.py`

- [ ] **Step 1: Write the failing test**

Add to `core/triggers/tests/test_engine.py`:

```python
@pytest.mark.asyncio
async def test_fire_without_action_propagates_urgency(
    mock_store: AsyncMock, mock_redis: AsyncMock
) -> None:
    import json

    from core.notifications.schema import Urgency
    from core.triggers.engine import TriggerEngine

    cls = TriggerRegistry.get("time")
    trigger = cls(
        trigger_id="t-1",
        trigger_type="time",
        name="urgent reminder",
        created_by="test",
        created_at=datetime.now(UTC),
        conditions={"cron": "0 7 * * *"},
        urgency=Urgency.URGENT,
    )

    engine = TriggerEngine(store=mock_store, redis=mock_redis)
    ctx = TriggerContext(now=datetime.now(UTC))
    await engine.fire(trigger, ctx)

    call_args = mock_redis.xadd.call_args
    event_json = json.loads(call_args[0][1]["event"])
    assert event_json["urgency"] == "urgent"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && .venv/bin/python -m pytest core/triggers/tests/test_engine.py::test_fire_without_action_propagates_urgency -v`
Expected: FAIL — `urgency` key missing from serialized event or defaults to `"informational"`

- [ ] **Step 3: Add urgency propagation to `fire()`**

In `core/triggers/engine.py`, modify the `TriggerFired` construction (lines 42-47):

```python
        else:
            event = TriggerFired(
                trigger_id=trigger.trigger_id,
                trigger_name=trigger.name,
                trigger_type=trigger.trigger_type,
                context=self._build_fire_context(trigger, context),
                urgency=trigger.urgency.value,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && .venv/bin/python -m pytest core/triggers/tests/test_engine.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
git add core/triggers/engine.py core/triggers/tests/test_engine.py
git commit -m "feat(triggers): propagate urgency from trigger to TriggerFired event"
```

---

### Task 4: Add urgency to TriggerFeature CRUD

**Files:**
- Modify: `core/triggers/feature.py:48-71,73-106,131-164`
- Modify: `core/triggers/tests/test_feature.py`

- [ ] **Step 1: Write the failing tests**

Add to `core/triggers/tests/test_feature.py`:

```python
@pytest.mark.asyncio
async def test_create_trigger_with_urgency(mock_store: AsyncMock) -> None:
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext

    f = TriggerFeature(ctx=TriggerFeatureContext(store=mock_store))
    result = await f.create_trigger(
        name="urgent reminder",
        trigger_type="time",
        conditions={"cron": "0 7 * * *"},
        urgency="urgent",
    )
    assert result["urgency"] == "urgent"
    mock_store.save.assert_called_once()


@pytest.mark.asyncio
async def test_create_trigger_default_urgency(mock_store: AsyncMock) -> None:
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext

    f = TriggerFeature(ctx=TriggerFeatureContext(store=mock_store))
    result = await f.create_trigger(
        name="chill reminder",
        trigger_type="time",
        conditions={"cron": "0 7 * * *"},
    )
    assert result["urgency"] == "informational"


@pytest.mark.asyncio
async def test_create_trigger_invalid_urgency(mock_store: AsyncMock) -> None:
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext

    f = TriggerFeature(ctx=TriggerFeatureContext(store=mock_store))
    result = await f.create_trigger(
        name="test",
        trigger_type="time",
        conditions={"cron": "0 7 * * *"},
        urgency="nonexistent",
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_update_trigger_urgency(mock_store: AsyncMock) -> None:
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext

    cls = TriggerRegistry.get("time")
    trigger = cls(
        trigger_id="t-1",
        trigger_type="time",
        name="test",
        created_by="test",
        created_at=datetime.now(UTC),
        conditions={"cron": "0 7 * * *"},
    )
    mock_store.get = AsyncMock(return_value=trigger)

    f = TriggerFeature(ctx=TriggerFeatureContext(store=mock_store))
    result = await f.update_trigger(trigger_id="t-1", urgency="important")
    assert result["urgency"] == "important"


@pytest.mark.asyncio
async def test_update_trigger_invalid_urgency(mock_store: AsyncMock) -> None:
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext

    cls = TriggerRegistry.get("time")
    trigger = cls(
        trigger_id="t-1",
        trigger_type="time",
        name="test",
        created_by="test",
        created_at=datetime.now(UTC),
        conditions={"cron": "0 7 * * *"},
    )
    mock_store.get = AsyncMock(return_value=trigger)

    f = TriggerFeature(ctx=TriggerFeatureContext(store=mock_store))
    result = await f.update_trigger(trigger_id="t-1", urgency="nonexistent")
    assert "error" in result


def test_dynamic_description_includes_urgency() -> None:
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext

    f = TriggerFeature(ctx=TriggerFeatureContext(store=AsyncMock()))
    tools = f.get_tools()
    create_tool = next(t for t in tools if "create_trigger" in t.name)
    assert "urgency" in create_tool.description
    assert "informational" in create_tool.description
    assert "important" in create_tool.description
    assert "urgent" in create_tool.description
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && .venv/bin/python -m pytest core/triggers/tests/test_feature.py::test_create_trigger_with_urgency core/triggers/tests/test_feature.py::test_create_trigger_invalid_urgency core/triggers/tests/test_feature.py::test_update_trigger_urgency core/triggers/tests/test_feature.py::test_dynamic_description_includes_urgency -v`
Expected: FAIL

- [ ] **Step 3: Update `create_trigger` with urgency parameter**

In `core/triggers/feature.py`, add import and modify `create_trigger`:

```python
# Add to imports
from core.notifications.schema import Urgency

# In create_trigger signature, add parameter:
    urgency: str = "informational",

# After action validation (line 91), before trigger construction:
    try:
        validated_urgency = Urgency(urgency)
    except ValueError:
        return {"error": f"Invalid urgency: {urgency}. Must be: informational, important, urgent"}

# In the cls() constructor call, add:
    urgency=validated_urgency,
```

- [ ] **Step 4: Update `update_trigger` with urgency parameter**

In `core/triggers/feature.py`, modify `update_trigger`:

```python
# Add to signature:
    urgency: str | None = None,

# After action validation block (before line 162 `updated = target.model_copy`):
    if urgency is not None:
        try:
            updates["urgency"] = Urgency(urgency)
        except ValueError:
            return {"error": f"Invalid urgency: {urgency}. Must be: informational, important, urgent"}
```

- [ ] **Step 5: Update `get_tools()` dynamic description**

In `core/triggers/feature.py`, modify `get_tools()` to append urgency docs:

```python
    urgency_docs = (
        "\n\nurgency (optional): \"informational\" | \"important\" | \"urgent\"\n"
        "Sets notification urgency when trigger fires without an action. Default: informational."
    )

    # In the enriched append for create_trigger:
    description=t.description + "\n\n" + conditions_docs + action_docs + urgency_docs,
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && .venv/bin/python -m pytest core/triggers/tests/test_feature.py -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
git add core/triggers/feature.py core/triggers/tests/test_feature.py
git commit -m "feat(triggers): add urgency param to create/update trigger CRUD"
```

---

### Task 5: Add `_parse_slm_json()` shared helper and `process_trigger_fired()` to ReflexEngine

**Files:**
- Modify: `core/reflex/engine.py`
- Modify: `core/reflex/tests/test_engine.py`

- [ ] **Step 1: Write the failing tests**

Add to `core/reflex/tests/test_engine.py`:

```python
from bus.schemas.events import TriggerFired


def _make_trigger_fired(
    name: str = "take medicine",
    trigger_type: str = "time",
    urgency: str = "informational",
) -> TriggerFired:
    return TriggerFired(
        trigger_id="t-1",
        trigger_name=name,
        trigger_type=trigger_type,
        context={"trigger_type": trigger_type, "evaluated_at": "2026-03-23T21:00:00Z"},
        urgency=urgency,
    )


@pytest.mark.asyncio
async def test_process_trigger_fired_produces_action(
    mock_memory_reader: MemoryReader,
    mock_registry: AsyncMock,
) -> None:
    from core.reflex.engine import ReflexEngine

    mock_ollama_response = {
        "response": json.dumps(
            {
                "tool_name": "lighting.dim_lights",
                "target_service": "home-service",
                "parameters": {"room": "bedroom", "level": 10},
            }
        ),
        "prompt_tokens": 200,
        "completion_tokens": 30,
        "total_tokens": 230,
    }

    with patch(
        "core.reflex.ollama_client.infer",
        new_callable=AsyncMock,
        return_value=mock_ollama_response,
    ):
        engine = ReflexEngine(
            preferences_dir="/fake/prefs",
            tool_registry=mock_registry,
            memory_reader=mock_memory_reader,
        )
        action = await engine.process_trigger_fired(_make_trigger_fired())

    assert action is not None
    assert action.tool_name == "lighting.dim_lights"
    assert action.source == "reflex-engine"


@pytest.mark.asyncio
async def test_process_trigger_fired_returns_none_for_no_action(
    mock_memory_reader: MemoryReader,
    mock_registry: AsyncMock,
) -> None:
    from core.reflex.engine import ReflexEngine

    mock_ollama_response = {
        "response": json.dumps({"action": "none"}),
        "prompt_tokens": 150,
        "completion_tokens": 10,
        "total_tokens": 160,
    }

    with patch(
        "core.reflex.ollama_client.infer",
        new_callable=AsyncMock,
        return_value=mock_ollama_response,
    ):
        engine = ReflexEngine(
            preferences_dir="/fake/prefs",
            tool_registry=mock_registry,
            memory_reader=mock_memory_reader,
        )
        action = await engine.process_trigger_fired(_make_trigger_fired())

    assert action is None


@pytest.mark.asyncio
async def test_process_trigger_fired_prompt_contains_trigger_details(
    mock_registry: AsyncMock,
    mock_memory_reader: MemoryReader,
) -> None:
    from core.reflex.engine import ReflexEngine

    mock_ollama_response = {
        "response": json.dumps({"action": "none"}),
    }

    with patch(
        "core.reflex.ollama_client.infer",
        new_callable=AsyncMock,
        return_value=mock_ollama_response,
    ) as mock_infer:
        engine = ReflexEngine(
            preferences_dir="/fake/prefs",
            tool_registry=mock_registry,
            memory_reader=mock_memory_reader,
        )
        await engine.process_trigger_fired(_make_trigger_fired())

    called_prompt = mock_infer.call_args[0][0]
    assert "## Trigger Fired" in called_prompt
    assert "take medicine" in called_prompt
    assert "time" in called_prompt
    assert "ALREADY being notified" in called_prompt


@pytest.mark.asyncio
async def test_process_trigger_fired_handles_malformed_json(
    mock_memory_reader: MemoryReader,
    mock_registry: AsyncMock,
) -> None:
    from core.reflex.engine import ReflexEngine

    mock_ollama_response = {"response": "not valid json at all"}

    with patch(
        "core.reflex.ollama_client.infer",
        new_callable=AsyncMock,
        return_value=mock_ollama_response,
    ):
        engine = ReflexEngine(
            preferences_dir="/fake/prefs",
            tool_registry=mock_registry,
            memory_reader=mock_memory_reader,
        )
        action = await engine.process_trigger_fired(_make_trigger_fired())

    assert action is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && .venv/bin/python -m pytest core/reflex/tests/test_engine.py::test_process_trigger_fired_produces_action -v`
Expected: FAIL — `ReflexEngine has no attribute 'process_trigger_fired'`

- [ ] **Step 3: Add `_build_notification_body()` to `engine.py`**

Add to `core/reflex/engine.py` after the existing `_build_tool_section` function:

```python
def _build_notification_body(event: TriggerFired) -> str:
    """Build a human-readable notification body from TriggerFired context."""
    parts: list[str] = []
    if event.context.get("event_entity"):
        entity = event.context["event_entity"]
        state = event.context.get("event_state")
        parts.append(f"{entity}: {state}" if state else str(entity))
    if event.context.get("evaluated_at"):
        parts.append(f"Fired at {event.context['evaluated_at']}")
    return " | ".join(parts) if parts else f"Trigger '{event.trigger_name}' fired"
```

- [ ] **Step 4: Add `_TRIGGER_FIRED_PROMPT_TEMPLATE` to `engine.py`**

Add after `_SYSTEM_PROMPT_TEMPLATE`:

```python
_TRIGGER_FIRED_PROMPT_TEMPLATE = """\
You are Alfred's Reflex Engine — a fast-acting steward for a smart home.

A trigger has fired. The user set up this trigger for a reason. Given the trigger details,
current home state, and user preferences, decide if any additional action is needed beyond
the notification already being sent to the user.

Rules:
- The user is ALREADY being notified about this trigger. You do NOT need to send a notification.
- Only act if an additional home automation action would be helpful given the context.
- If no additional action is needed, respond with: {{"action": "none"}}
- If an action IS needed, respond with:
  {{"tool_name": "<tool name>", "target_service": "<service>", "parameters": {{<params>}}}}

{tool_section}

Respond ONLY with valid JSON. No explanation."""
```

- [ ] **Step 5: Add `_parse_slm_json()` shared helper**

Add as a method on `ReflexEngine`:

```python
    def _parse_slm_json(
        self,
        response: dict[str, object],
        valid_services: set[str],
        log_label: str,
    ) -> ActionRequest | None:
        """Shared SLM JSON response parser."""
        try:
            raw = response.get("response", "")
            parsed = json.loads(str(raw))

            if parsed.get("action") == "none":
                logger.debug("No action for %s", log_label)
                return None

            tool_name = parsed.get("tool_name")
            if not tool_name:
                logger.warning("SLM response missing tool_name: %s", raw)
                return None

            target_service = str(parsed.get("target_service", ""))
            if target_service not in valid_services:
                logger.warning(
                    "SLM returned unregistered target_service: %s (valid: %s)",
                    target_service,
                    valid_services,
                )
                return None

            return ActionRequest(
                source="reflex-engine",
                target_service=target_service,
                tool_name=str(tool_name),
                parameters=dict(parsed.get("parameters", {})),
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.error("Failed to parse SLM response: %s — %s", e, response)
            return None
```

- [ ] **Step 6: Refactor `parse_response` to delegate to `_parse_slm_json`**

Replace the body of existing `parse_response`:

```python
    def parse_response(
        self,
        response: dict[str, object],
        event: StateChangedEvent,
        valid_services: set[str],
    ) -> ActionRequest | None:
        """Parse the SLM's JSON response into an ActionRequest or None."""
        return self._parse_slm_json(response, valid_services, log_label=event.entity_id)
```

- [ ] **Step 7: Add `parse_trigger_response` method**

```python
    def parse_trigger_response(
        self,
        response: dict[str, object],
        event: TriggerFired,
        valid_services: set[str],
    ) -> ActionRequest | None:
        """Parse SLM response for a TriggerFired event."""
        return self._parse_slm_json(response, valid_services, log_label=event.trigger_name)
```

- [ ] **Step 8: Add `process_trigger_fired` method**

```python
    @traced(name="reflex.process_trigger_fired")
    @track_latency(category="reflex")
    async def process_trigger_fired(self, event: TriggerFired) -> ActionRequest | None:
        """Process a TriggerFired event and optionally produce an action."""
        preferences = self._get_preferences()
        tools, _ = await self._get_tools_and_prompt()
        valid_services = ToolRegistry.get_registered_services(tools)

        tool_section = _build_tool_section(tools)
        system_prompt = _TRIGGER_FIRED_PROMPT_TEMPLATE.format(tool_section=tool_section)

        context = ""
        if self._context_reader is not None:
            context = await self._context_reader.get_rendered_context()

        context_section = f"## Home State\n{context}\n\n" if context else ""
        prompt = (
            f"{system_prompt}\n\n"
            f"{context_section}"
            f"## User Preferences\n{preferences}\n\n"
            f"## Trigger Fired\n"
            f"Name: {event.trigger_name}\n"
            f"Type: {event.trigger_type}\n"
            f"Context: {json.dumps(event.context)}\n\n"
            f"## Your Decision (JSON only):"
        )

        response = await ollama_client.infer(prompt)
        return self.parse_trigger_response(response, event, valid_services)
```

- [ ] **Step 9: Add `TriggerFired` import**

Add to imports in `core/reflex/engine.py`:

```python
from bus.schemas.events import ActionRequest, StateChangedEvent, TriggerFired
```

- [ ] **Step 10: Run all tests to verify**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && .venv/bin/python -m pytest core/reflex/tests/test_engine.py -v`
Expected: ALL PASS (both existing `StateChangedEvent` tests and new `TriggerFired` tests)

- [ ] **Step 11: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
git add core/reflex/engine.py core/reflex/tests/test_engine.py
git commit -m "feat(reflex): add process_trigger_fired and shared SLM parser"
```

---

### Task 6: Add `_build_notification_body` tests

**Files:**
- Modify: `core/reflex/tests/test_engine.py`

- [ ] **Step 1: Write the tests**

Add to `core/reflex/tests/test_engine.py`:

```python
def test_build_notification_body_sensor_with_state() -> None:
    from core.reflex.engine import _build_notification_body

    evt = TriggerFired(
        trigger_id="t-1",
        trigger_name="laundry done",
        trigger_type="sensor",
        context={
            "event_entity": "sensor.washing_machine_power",
            "event_state": "idle",
            "evaluated_at": "2026-03-23T21:00:00Z",
        },
    )
    body = _build_notification_body(evt)
    assert "sensor.washing_machine_power: idle" in body
    assert "Fired at 2026-03-23T21:00:00Z" in body


def test_build_notification_body_sensor_without_state() -> None:
    from core.reflex.engine import _build_notification_body

    evt = TriggerFired(
        trigger_id="t-1",
        trigger_name="test",
        trigger_type="sensor",
        context={"event_entity": "sensor.temp", "evaluated_at": "2026-03-23T21:00:00Z"},
    )
    body = _build_notification_body(evt)
    assert "sensor.temp" in body
    assert "sensor.temp:" not in body  # no trailing colon when state is missing


def test_build_notification_body_time_trigger() -> None:
    from core.reflex.engine import _build_notification_body

    evt = TriggerFired(
        trigger_id="t-1",
        trigger_name="take medicine",
        trigger_type="time",
        context={"trigger_type": "time", "evaluated_at": "2026-03-23T21:00:00Z"},
    )
    body = _build_notification_body(evt)
    assert "Fired at 2026-03-23T21:00:00Z" in body


def test_build_notification_body_empty_context() -> None:
    from core.reflex.engine import _build_notification_body

    evt = TriggerFired(
        trigger_id="t-1",
        trigger_name="my trigger",
        trigger_type="time",
        context={},
    )
    body = _build_notification_body(evt)
    assert "my trigger" in body
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && .venv/bin/python -m pytest core/reflex/tests/test_engine.py::test_build_notification_body_sensor_with_state core/reflex/tests/test_engine.py::test_build_notification_body_time_trigger core/reflex/tests/test_engine.py::test_build_notification_body_empty_context -v`
Expected: ALL PASS (implementation already added in Task 5)

- [ ] **Step 3: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
git add core/reflex/tests/test_engine.py
git commit -m "test(reflex): add notification body builder tests"
```

---

### Task 7: Add second event loop + notification wiring to Reflex Runner

**Files:**
- Modify: `core/reflex/__main__.py`
- Create: `core/reflex/tests/test_trigger_fired_consumer.py`

- [ ] **Step 1: Write the integration test**

Create `core/reflex/tests/test_trigger_fired_consumer.py`:

```python
"""Integration tests for the TriggerFired consumer in the Reflex Runner."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from bus.schemas.events import TriggerFired
from core.notifications.schema import Urgency


@pytest.fixture
def mock_publisher() -> AsyncMock:
    publisher = AsyncMock()
    publisher.publish = AsyncMock()
    return publisher


@pytest.fixture
def mock_engine() -> AsyncMock:
    engine = AsyncMock()
    engine.process_trigger_fired = AsyncMock(return_value=None)
    return engine


@pytest.fixture
def mock_agent() -> AsyncMock:
    return AsyncMock()


def _make_entry_data(event: TriggerFired) -> dict[str, str]:
    return {"event": event.model_dump_json()}


@pytest.mark.asyncio
async def test_handle_trigger_fired_publishes_notification(
    mock_engine: AsyncMock,
    mock_agent: AsyncMock,
    mock_publisher: AsyncMock,
) -> None:
    from core.reflex.__main__ import _handle_trigger_fired

    event = TriggerFired(
        trigger_id="t-1",
        trigger_name="take medicine",
        trigger_type="time",
        urgency="important",
    )
    redis = AsyncMock()

    await _handle_trigger_fired(
        _make_entry_data(event), mock_engine, mock_agent, redis, mock_publisher,
    )

    mock_publisher.publish.assert_called_once()
    call_kwargs = mock_publisher.publish.call_args
    assert call_kwargs.kwargs["urgency"] == Urgency.IMPORTANT
    assert "Reminder:" in call_kwargs.kwargs["title"]


@pytest.mark.asyncio
async def test_handle_trigger_fired_sensor_uses_alert_title(
    mock_engine: AsyncMock,
    mock_agent: AsyncMock,
    mock_publisher: AsyncMock,
) -> None:
    from core.reflex.__main__ import _handle_trigger_fired

    event = TriggerFired(
        trigger_id="t-1",
        trigger_name="humidity high",
        trigger_type="sensor",
    )
    redis = AsyncMock()

    await _handle_trigger_fired(
        _make_entry_data(event), mock_engine, mock_agent, redis, mock_publisher,
    )

    call_kwargs = mock_publisher.publish.call_args
    assert "Alert:" in call_kwargs.kwargs["title"]


@pytest.mark.asyncio
async def test_handle_trigger_fired_calls_slm(
    mock_engine: AsyncMock,
    mock_agent: AsyncMock,
    mock_publisher: AsyncMock,
) -> None:
    from core.reflex.__main__ import _handle_trigger_fired

    event = TriggerFired(
        trigger_id="t-1",
        trigger_name="test",
        trigger_type="time",
    )
    redis = AsyncMock()

    await _handle_trigger_fired(
        _make_entry_data(event), mock_engine, mock_agent, redis, mock_publisher,
    )

    mock_engine.process_trigger_fired.assert_called_once()


@pytest.mark.asyncio
async def test_handle_trigger_fired_slm_failure_does_not_block_notification(
    mock_agent: AsyncMock,
    mock_publisher: AsyncMock,
) -> None:
    from core.reflex.__main__ import _handle_trigger_fired

    engine = AsyncMock()
    engine.process_trigger_fired = AsyncMock(side_effect=RuntimeError("Ollama down"))

    event = TriggerFired(
        trigger_id="t-1",
        trigger_name="test",
        trigger_type="time",
    )
    redis = AsyncMock()

    # Should NOT raise — SLM error is caught, notification already sent
    await _handle_trigger_fired(
        _make_entry_data(event), engine, mock_agent, redis, mock_publisher,
    )

    mock_publisher.publish.assert_called_once()


@pytest.mark.asyncio
async def test_handle_trigger_fired_skips_non_trigger_events(
    mock_engine: AsyncMock,
    mock_agent: AsyncMock,
    mock_publisher: AsyncMock,
) -> None:
    from core.reflex.__main__ import _handle_trigger_fired

    entry_data = {"event": json.dumps({"event_type": "state_changed", "source": "test"})}
    redis = AsyncMock()

    await _handle_trigger_fired(
        entry_data, mock_engine, mock_agent, redis, mock_publisher,
    )

    mock_publisher.publish.assert_not_called()
    mock_engine.process_trigger_fired.assert_not_called()


@pytest.mark.asyncio
async def test_handle_trigger_fired_skips_missing_event_field(
    mock_engine: AsyncMock,
    mock_agent: AsyncMock,
    mock_publisher: AsyncMock,
) -> None:
    from core.reflex.__main__ import _handle_trigger_fired

    redis = AsyncMock()

    await _handle_trigger_fired(
        {"not_event": "data"}, mock_engine, mock_agent, redis, mock_publisher,
    )

    mock_publisher.publish.assert_not_called()


@pytest.mark.asyncio
async def test_handle_trigger_fired_dnd_defers_informational(
    mock_engine: AsyncMock,
    mock_agent: AsyncMock,
) -> None:
    """DND active + INFORMATIONAL urgency -> notification deferred."""
    from core.notifications.dnd import DNDChecker
    from core.notifications.dispatcher import NotificationDispatcher
    from core.notifications.publisher import NotificationPublisher
    from core.notifications.schema import DNDStatus
    from core.reflex.__main__ import _handle_trigger_fired

    redis = AsyncMock()
    redis.rpush = AsyncMock()
    redis.xadd = AsyncMock()

    dnd_checker = AsyncMock(spec=DNDChecker)
    dnd_checker.is_active = AsyncMock(
        return_value=DNDStatus(active=True, reason="manual", source="manual")
    )
    dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd_checker)
    publisher = NotificationPublisher(dispatcher)

    event = TriggerFired(
        trigger_id="t-1",
        trigger_name="low priority",
        trigger_type="time",
        urgency="informational",
    )

    await _handle_trigger_fired(
        _make_entry_data(event), mock_engine, mock_agent, redis, publisher,
    )

    # Should defer (rpush to deferred queue), NOT deliver (no xadd to dispatch stream)
    redis.rpush.assert_called_once()
    redis.xadd.assert_not_called()


@pytest.mark.asyncio
async def test_handle_trigger_fired_dnd_delivers_urgent(
    mock_engine: AsyncMock,
    mock_agent: AsyncMock,
) -> None:
    """DND active + URGENT urgency -> notification delivered immediately."""
    from core.notifications.dnd import DNDChecker
    from core.notifications.dispatcher import NotificationDispatcher
    from core.notifications.publisher import NotificationPublisher
    from core.notifications.schema import DNDStatus
    from core.reflex.__main__ import _handle_trigger_fired

    redis = AsyncMock()
    redis.rpush = AsyncMock()
    redis.xadd = AsyncMock()

    dnd_checker = AsyncMock(spec=DNDChecker)
    dnd_checker.is_active = AsyncMock(
        return_value=DNDStatus(active=True, reason="manual", source="manual")
    )
    dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd_checker)
    publisher = NotificationPublisher(dispatcher)

    event = TriggerFired(
        trigger_id="t-1",
        trigger_name="critical alert",
        trigger_type="sensor",
        urgency="urgent",
    )

    await _handle_trigger_fired(
        _make_entry_data(event), mock_engine, mock_agent, redis, publisher,
    )

    # Should deliver (xadd to dispatch stream), NOT defer
    redis.xadd.assert_called_once()
    redis.rpush.assert_not_called()


@pytest.mark.asyncio
async def test_handle_trigger_fired_with_slm_action(
    mock_agent: AsyncMock,
    mock_publisher: AsyncMock,
) -> None:
    from bus.schemas.events import ActionResult
    from core.reflex.__main__ import _handle_trigger_fired

    action_result = ActionResult(
        source="home-service",
        request_id="r-1",
        tool_name="lighting.dim_lights",
        status="success",
    )
    mock_agent.execute_action = AsyncMock(return_value=action_result)

    engine = AsyncMock()
    from bus.schemas.events import ActionRequest
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
    redis.lpush = AsyncMock()

    await _handle_trigger_fired(
        _make_entry_data(event), engine, mock_agent, redis, mock_publisher,
    )

    mock_publisher.publish.assert_called_once()
    mock_agent.execute_action.assert_called_once()
    redis.xadd.assert_called_once()  # result stream
    redis.lpush.assert_called_once()  # scratchpad
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && .venv/bin/python -m pytest core/reflex/tests/test_trigger_fired_consumer.py::test_handle_trigger_fired_publishes_notification -v`
Expected: FAIL — `cannot import name '_handle_trigger_fired' from 'core.reflex.__main__'`

- [ ] **Step 3: Implement `_handle_trigger_fired` in `__main__.py`**

Add to `core/reflex/__main__.py`, after existing imports:

```python
import json
from collections.abc import Mapping
from datetime import UTC, datetime

from bus.schemas.events import TriggerFired
from core.notifications.dnd import DNDChecker
from core.notifications.dispatcher import NotificationDispatcher
from core.notifications.publisher import NotificationPublisher
from core.notifications.schema import Urgency
from core.reflex.engine import _build_notification_body
from shared.streams import EVENTS_STREAM, HOME_ACTION_RESULTS_STREAM
```

Update the existing `shared.streams` import to include `EVENTS_STREAM` and ensure `decode_stream_value` is imported (it's in `shared.streams` — used via `from shared.streams import decode_stream_value`).

Add the handler function:

```python
async def _handle_trigger_fired(
    entry_data: Mapping[str | bytes, str | bytes],
    engine: ReflexEngine,
    agent: DomainRouter,
    redis: AioRedis,
    publisher: NotificationPublisher,
) -> None:
    """Handle a single TriggerFired event — notify + optional SLM reasoning."""
    raw_event = entry_data.get("event") or entry_data.get(b"event")
    if raw_event is None:
        return

    event_str = decode_stream_value(raw_event)
    parsed = json.loads(event_str)

    if parsed.get("event_type") != "trigger_fired":
        return

    trigger_event = TriggerFired.model_validate(parsed)

    # Path A: Immediate notification (DND-aware via dispatcher)
    urgency = Urgency(trigger_event.urgency)
    title = (
        f"Reminder: {trigger_event.trigger_name}"
        if trigger_event.trigger_type == "time"
        else f"Alert: {trigger_event.trigger_name}"
    )
    await publisher.publish(
        title=title,
        body=_build_notification_body(trigger_event),
        source="trigger-engine",
        urgency=urgency,
    )

    # Path B: Reflex SLM reasoning (isolated — failures don't block ACK)
    try:
        action = await engine.process_trigger_fired(trigger_event)
        if action is not None:
            result = await agent.execute_action(action)
            await redis.xadd(
                HOME_ACTION_RESULTS_STREAM, {"event": result.model_dump_json()}
            )

            timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            observation = (
                f"{timestamp} [reflex:trigger] "
                f"{action.tool_name}({action.parameters}) -> {result.status}"
            )
            await redis.lpush(SCRATCHPAD_QUEUE, observation)
    except Exception as e:
        logger.error(
            "SLM reasoning failed for trigger '%s': %s", trigger_event.trigger_name, e
        )
```

- [ ] **Step 4: Add `_consume_trigger_fired` event loop**

Add to `core/reflex/__main__.py`:

```python
EVENTS_GROUP = "reflex-trigger-fired"
EVENTS_CONSUMER = "worker-1"


async def _consume_trigger_fired(
    redis: AioRedis,
    engine: ReflexEngine,
    agent: DomainRouter,
    publisher: NotificationPublisher,
) -> None:
    """Second event loop — TriggerFired events from alfred:events."""
    await ensure_consumer_group(redis, EVENTS_STREAM, EVENTS_GROUP)

    while not _shutdown.is_set():
        entries: list[
            tuple[
                bytes | str,
                list[tuple[bytes | str, dict[bytes | str, bytes | str]]],
            ]
        ] = await redis.xreadgroup(
            EVENTS_GROUP, EVENTS_CONSUMER,
            {EVENTS_STREAM: ">"}, count=10, block=5000,
        )
        for _stream_key, stream_entries in entries:
            for entry_id, entry_data in stream_entries:
                try:
                    await _handle_trigger_fired(
                        entry_data, engine, agent, redis, publisher,
                    )
                    await redis.xack(EVENTS_STREAM, EVENTS_GROUP, entry_id)  # type: ignore[no-untyped-call]
                except Exception as e:
                    logger.error(
                        "Error processing trigger_fired %s: %s — will retry",
                        entry_id, e,
                    )
```

- [ ] **Step 5: Wire into `run()` function**

In the `run()` function, after `writer = ScratchpadWriter(...)` and before `# Background tasks`:

```python
    # Notification wiring for TriggerFired (no calendar adapter, no trigger store)
    dnd_checker = DNDChecker(redis=r, calendar_adapter=None)
    dispatcher = NotificationDispatcher(redis=r, dnd_checker=dnd_checker)
    publisher = NotificationPublisher(dispatcher)
```

After the existing `telemetry_task` line:

```python
    trigger_fired_task = asyncio.create_task(
        _consume_trigger_fired(r, engine, router, publisher)
    )
```

In the `finally` block, add before `await r.aclose()`:

```python
        trigger_fired_task.cancel()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && .venv/bin/python -m pytest core/reflex/tests/test_trigger_fired_consumer.py -v`
Expected: ALL PASS

- [ ] **Step 7: Run full test suite**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && .venv/bin/python -m pytest -x -q`
Expected: ALL PASS

- [ ] **Step 8: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
git add core/reflex/__main__.py core/reflex/tests/test_trigger_fired_consumer.py
git commit -m "feat(reflex): add TriggerFired consumer with notification + SLM reasoning"
```

---

### Task 8: Lint, type-check, and verify

**Files:**
- All modified files

- [ ] **Step 1: Run ruff**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
ruff check . --fix && ruff format .
```
Expected: No errors (or auto-fixed)

- [ ] **Step 2: Run mypy**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
mypy --strict bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/
```
Expected: No new errors

- [ ] **Step 3: Fix any issues and commit**

```bash
git add bus/ core/ && git commit -m "chore: fix lint and type errors from D22"
```

---

### Task 9: Mark D22 as done in backlog

**Files:**
- Modify: `docs/backlog/remaining-work.md`

- [ ] **Step 1: Mark D22 as done**

In `docs/backlog/remaining-work.md`, update the D22 row:

```markdown
| ~~D22~~ | ~~TriggerFired → user notification bridge~~ | ~~Section 1+8~~ | DONE — TriggerFired events consumed by Reflex process: immediate DND-aware notification + SLM reasoning for additional actions. Urgency field on BaseTrigger and TriggerFired |
```

- [ ] **Step 2: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
git add docs/backlog/remaining-work.md
git commit -m "docs: mark D22 as done in backlog"
```

---

### Task 10: Code architect review

Use the `superpowers:code-reviewer` agent to review all changes against the spec and architecture rules.

- [ ] **Step 1: Run code architect review**
- [ ] **Step 2: Fix any issues found**
- [ ] **Step 3: Commit fixes**

---

### Task 11: Simplify

Use the `/simplify` skill to review changed code for quality, reuse, and efficiency.

- [ ] **Step 1: Run simplify**
- [ ] **Step 2: Fix any issues found**
- [ ] **Step 3: Commit fixes**

---

### Task 12: Update CLAUDE.md

Use the `claude-md-management:revise-claude-md` skill to update project context.

- [ ] **Step 1: Run claude-md revision**
- [ ] **Step 2: Commit updates**

---

### Task 13: Create PR

- [ ] **Step 1: Create PR with all D22 changes**
