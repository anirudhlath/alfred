# Trigger Engine Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Trigger Engine — a proactive core service that fires actions on time schedules, sensor conditions, or composite rules, with CRUD exposed via BaseFeature.

**Architecture:** Dedicated async service in `core/triggers/` with decorator-based type registry (`BaseTrigger` ABC), Redis storage + YAML disk snapshots, dual evaluation loops (1s tick + Redis Stream event listener), and tool dispatch via HTTP/JSON-RPC. Follows the Reflex Runner pattern exactly.

**Tech Stack:** Python 3.13, Pydantic v2, redis-py async, croniter, PyYAML, pytest + pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-03-10-trigger-engine-design.md`

---

## Chunk 1: Models, Registry, and Trigger Types

### Task 1: Event Schema Updates

**Files:**
- Modify: `bus/schemas/events.py`
- Test: `bus/schemas/tests/test_events.py`

- [ ] **Step 1: Write failing tests for new/updated event types**

```python
# bus/schemas/tests/test_events.py — append these tests

def test_trigger_fired_defaults() -> None:
    evt = TriggerFired(
        trigger_id="t-1",
        trigger_name="test trigger",
        trigger_type="time",
        context={"reason": "cron matched"},
    )
    assert evt.event_type == "trigger_fired"
    assert evt.source == "trigger-engine"
    assert evt.trigger_id == "t-1"


def test_trigger_created_updated_schema() -> None:
    evt = TriggerCreated(
        trigger_id="t-1",
        trigger_type="sensor",
        name="dim on TV",
        created_by="reflex-engine",
        conditions={"entity_id": "media_player.tv", "state_match": "on"},
    )
    assert evt.event_type == "trigger_created"
    assert evt.source == "trigger-engine"
    assert evt.action is None
    assert evt.one_shot is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest bus/schemas/tests/test_events.py -v -k "trigger_fired or trigger_created_updated"`
Expected: FAIL — `TriggerFired` not importable, `TriggerCreated` signature mismatch

- [ ] **Step 3: Update event schemas**

Replace the existing `TriggerCreated` and add `TriggerFired` in `bus/schemas/events.py`:

```python
class TriggerFired(BaseEvent):
    """A trigger's conditions were met. Emitted when trigger has no direct action."""

    event_type: str = "trigger_fired"
    source: str = "trigger-engine"
    trigger_id: str
    trigger_name: str
    trigger_type: str
    context: dict[str, Any] = Field(default_factory=dict)


class TriggerCreated(BaseEvent):
    """A trigger was dynamically created."""

    event_type: str = "trigger_created"
    source: str = "trigger-engine"
    trigger_id: str = Field(default_factory=lambda: str(uuid4()))
    trigger_type: str = Field(description="Registered trigger type (e.g. time, sensor, composite)")
    name: str
    created_by: str
    conditions: dict[str, Any] = Field(description="Trigger-type-specific conditions")
    action: dict[str, Any] | None = None
    one_shot: bool = False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest bus/schemas/tests/test_events.py -v`
Expected: ALL PASS

- [ ] **Step 5: Run linting and type checking**

Run: `uv run ruff check bus/ --fix && uv run ruff format bus/ && uv run mypy bus/`
Expected: Clean

- [ ] **Step 6: Commit**

```bash
git add bus/schemas/events.py bus/schemas/tests/test_events.py
git commit -m "feat(bus): add TriggerFired event and update TriggerCreated schema for Phase 2"
```

---

### Task 2: Add Dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add croniter and pyyaml to dependencies**

Add to the `dependencies` list in `pyproject.toml`:

```
    "croniter>=3.0",
    "pyyaml>=6.0",
```

- [ ] **Step 2: Install updated dependencies**

Run: `uv pip install -e ".[dev]"`
Expected: Success, croniter and pyyaml installed

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "build: add croniter and pyyaml for Trigger Engine"
```

---

### Task 3: BaseTrigger Models and ActionPayload

**Files:**
- Create: `core/triggers/__init__.py`
- Create: `core/triggers/models.py`
- Test: `core/triggers/tests/__init__.py`
- Test: `core/triggers/tests/test_models.py`

- [ ] **Step 1: Create `__init__.py` files**

Create empty `core/triggers/__init__.py` and `core/triggers/tests/__init__.py`.

- [ ] **Step 2: Write failing tests for models**

```python
# core/triggers/tests/test_models.py
"""Tests for trigger base models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from core.triggers.models import ActionPayload, TriggerContext


def test_action_payload_valid() -> None:
    ap = ActionPayload(
        tool_name="smart_home.dim_lights",
        target_service="home-service",
        parameters={"room": "living_room", "level": 30},
    )
    assert ap.tool_name == "smart_home.dim_lights"
    assert ap.parameters["level"] == 30


def test_action_payload_defaults() -> None:
    ap = ActionPayload(tool_name="x", target_service="y")
    assert ap.parameters == {}


def test_trigger_context_no_event() -> None:
    ctx = TriggerContext(now=datetime.now(UTC))
    assert ctx.event is None


def test_trigger_context_with_event() -> None:
    from bus.schemas.events import StateChangedEvent

    evt = StateChangedEvent(
        source="test", domain="home", entity_id="light.x", new_state="on"
    )
    ctx = TriggerContext(now=datetime.now(UTC), event=evt)
    assert ctx.event is not None
    assert ctx.event.entity_id == "light.x"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest core/triggers/tests/test_models.py -v`
Expected: FAIL — `core.triggers.models` not found

- [ ] **Step 4: Implement models**

```python
# core/triggers/models.py
"""Base trigger models: BaseTrigger ABC, ActionPayload, TriggerContext."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from bus.schemas.events import StateChangedEvent


class ActionPayload(BaseModel):
    """Action to execute when a trigger fires.

    Contains the subset of ActionRequest fields needed to describe the action.
    The Trigger Engine converts this to a full ActionRequest on fire, setting
    source='trigger-engine' and generating event metadata.
    """

    tool_name: str
    target_service: str
    parameters: dict[str, Any] = {}


class TriggerContext(BaseModel):
    """Read-only context passed to evaluate()."""

    now: datetime
    event: StateChangedEvent | None = None


class BaseTrigger(ABC, BaseModel):
    """Abstract trigger. Subclasses define evaluation logic and conditions schema.

    Every concrete subclass MUST define a `conditions` field typed to its own
    nested `Conditions` Pydantic model.
    """

    trigger_id: str
    trigger_type: str
    name: str
    enabled: bool = True
    one_shot: bool = False
    created_by: str
    created_at: datetime
    last_fired: datetime | None = None
    action: ActionPayload | None = None

    @abstractmethod
    def evaluate(self, context: TriggerContext) -> bool:
        """Return True if this trigger should fire now."""
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest core/triggers/tests/test_models.py -v`
Expected: ALL PASS

- [ ] **Step 6: Run linting and type checking**

Run: `uv run ruff check core/triggers/ --fix && uv run ruff format core/triggers/ && uv run mypy core/triggers/`
Expected: Clean

- [ ] **Step 7: Commit**

```bash
git add core/triggers/__init__.py core/triggers/models.py core/triggers/tests/__init__.py core/triggers/tests/test_models.py
git commit -m "feat(triggers): add BaseTrigger ABC, ActionPayload, and TriggerContext models"
```

---

### Task 4: TriggerRegistry

**Files:**
- Create: `core/triggers/registry.py`
- Test: `core/triggers/tests/test_registry.py`

- [ ] **Step 1: Write failing tests**

```python
# core/triggers/tests/test_registry.py
"""Tests for TriggerRegistry — decorator-based type registration."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import BaseModel

from core.triggers.models import BaseTrigger, TriggerContext
from core.triggers.registry import TriggerRegistry


class _DummyTrigger(BaseTrigger):
    """Test trigger type for registry tests."""

    trigger_type: str = "dummy"

    class Conditions(BaseModel):
        foo: str = "bar"

    conditions: Conditions = Conditions()

    def evaluate(self, context: TriggerContext) -> bool:
        return True


def test_register_and_get(monkeypatch: pytest.MonkeyPatch) -> None:
    # Clear registry for isolation
    monkeypatch.setattr(TriggerRegistry, "_registry", {})
    TriggerRegistry.register_type("dummy")(_DummyTrigger)
    assert TriggerRegistry.get("dummy") is _DummyTrigger


def test_get_unknown_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TriggerRegistry, "_registry", {})
    with pytest.raises(KeyError, match="nope"):
        TriggerRegistry.get("nope")


def test_available_types(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TriggerRegistry, "_registry", {})
    TriggerRegistry.register_type("alpha")(_DummyTrigger)
    TriggerRegistry.register_type("beta")(_DummyTrigger)
    assert sorted(TriggerRegistry.available_types()) == ["alpha", "beta"]


def test_build_conditions_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TriggerRegistry, "_registry", {})
    TriggerRegistry.register_type("dummy")(_DummyTrigger)
    docs = TriggerRegistry.build_conditions_docs()
    assert "dummy" in docs
    assert "foo" in docs
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest core/triggers/tests/test_registry.py -v`
Expected: FAIL — `core.triggers.registry` not found

- [ ] **Step 3: Implement TriggerRegistry**

```python
# core/triggers/registry.py
"""TriggerRegistry — decorator-based, open trigger type registration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from core.triggers.models import BaseTrigger


class TriggerRegistry:
    """Maps trigger_type strings to BaseTrigger subclasses."""

    _registry: dict[str, type[BaseTrigger]] = {}

    @classmethod
    def register_type(cls, trigger_type: str) -> Callable[[type[BaseTrigger]], type[BaseTrigger]]:
        """Class decorator to register a trigger type.

        Usage::

            @TriggerRegistry.register_type("time")
            class TimeTrigger(BaseTrigger):
                ...
        """

        def decorator(trigger_cls: type[BaseTrigger]) -> type[BaseTrigger]:
            cls._registry[trigger_type] = trigger_cls
            return trigger_cls

        return decorator

    @classmethod
    def get(cls, trigger_type: str) -> type[BaseTrigger]:
        """Look up a trigger class by type string. Raises KeyError if unknown."""
        try:
            return cls._registry[trigger_type]
        except KeyError:
            raise KeyError(
                f"Unknown trigger type: {trigger_type!r}. "
                f"Available: {list(cls._registry.keys())}"
            ) from None

    @classmethod
    def available_types(cls) -> list[str]:
        """Return all registered trigger type names."""
        return list(cls._registry.keys())

    @classmethod
    def build_conditions_docs(cls) -> str:
        """Introspect all registered types and their Conditions schemas.

        Returns a formatted string suitable for dynamic tool descriptions.
        """
        lines: list[str] = ["Available trigger types and their conditions:"]
        for type_name, trigger_cls in sorted(cls._registry.items()):
            conditions_cls: Any = getattr(trigger_cls, "Conditions", None)
            if conditions_cls is None:
                lines.append(f"  - {type_name}: (no conditions schema)")
                continue

            fields: dict[str, Any] = {}
            if hasattr(conditions_cls, "model_fields"):
                for fname, finfo in conditions_cls.model_fields.items():
                    annotation = finfo.annotation
                    type_str = getattr(annotation, "__name__", str(annotation))
                    required = finfo.is_required()
                    desc = finfo.description or ""
                    key = fname if required else f"{fname}?"
                    val = f"{type_str}" + (f" ({desc})" if desc else "")
                    fields[key] = val

            fields_str = ", ".join(f"{k}: {v}" for k, v in fields.items())
            lines.append(f"  - {type_name}: {{{fields_str}}}")

        return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest core/triggers/tests/test_registry.py -v`
Expected: ALL PASS

- [ ] **Step 5: Lint and type check**

Run: `uv run ruff check core/triggers/ --fix && uv run ruff format core/triggers/ && uv run mypy core/triggers/`
Expected: Clean

- [ ] **Step 6: Commit**

```bash
git add core/triggers/registry.py core/triggers/tests/test_registry.py
git commit -m "feat(triggers): add TriggerRegistry with decorator-based type registration"
```

---

### Task 5: Concrete Trigger Types — TimeTrigger

**Files:**
- Create: `core/triggers/types/__init__.py`
- Create: `core/triggers/types/time.py`
- Test: `core/triggers/tests/test_types_time.py`

- [ ] **Step 1: Write failing tests**

```python
# core/triggers/tests/test_types_time.py
"""Tests for TimeTrigger."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.triggers.models import TriggerContext
from core.triggers.registry import TriggerRegistry


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TriggerRegistry, "_registry", {})
    import core.triggers.types.time  # noqa: F401  — triggers registration


def _make_time_trigger(**kwargs: object) -> object:
    cls = TriggerRegistry.get("time")
    defaults = {
        "trigger_id": "t-1",
        "trigger_type": "time",
        "name": "test",
        "created_by": "test",
        "created_at": datetime.now(UTC),
        "conditions": {},
    }
    defaults.update(kwargs)
    return cls(**defaults)


def test_time_trigger_registered() -> None:
    assert "time" in TriggerRegistry.available_types()


def test_cron_match() -> None:
    # "0 7 * * *" = 07:00 daily
    trigger = _make_time_trigger(conditions={"cron": "0 7 * * *"})
    # Context at exactly 07:00:00
    ctx = TriggerContext(now=datetime(2026, 3, 10, 7, 0, 0, tzinfo=UTC))
    assert trigger.evaluate(ctx) is True


def test_cron_no_match() -> None:
    trigger = _make_time_trigger(conditions={"cron": "0 7 * * *"})
    ctx = TriggerContext(now=datetime(2026, 3, 10, 8, 0, 0, tzinfo=UTC))
    assert trigger.evaluate(ctx) is False


def test_run_at_fires() -> None:
    target = datetime(2026, 3, 10, 15, 0, 0, tzinfo=UTC)
    trigger = _make_time_trigger(conditions={"run_at": target.isoformat()})
    ctx = TriggerContext(now=datetime(2026, 3, 10, 15, 0, 1, tzinfo=UTC))
    assert trigger.evaluate(ctx) is True


def test_run_at_not_yet() -> None:
    target = datetime(2026, 3, 10, 15, 0, 0, tzinfo=UTC)
    trigger = _make_time_trigger(conditions={"run_at": target.isoformat()})
    ctx = TriggerContext(now=datetime(2026, 3, 10, 14, 59, 0, tzinfo=UTC))
    assert trigger.evaluate(ctx) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest core/triggers/tests/test_types_time.py -v`
Expected: FAIL

- [ ] **Step 3: Implement TimeTrigger**

```python
# core/triggers/types/__init__.py
"""Auto-import all trigger types so they register via decorators."""

from core.triggers.types import composite, sensor, time  # noqa: F401
```

```python
# core/triggers/types/time.py
"""TimeTrigger — fires on cron schedule or specific datetime."""

from __future__ import annotations

from datetime import UTC, datetime

from croniter import croniter
from pydantic import BaseModel

from core.triggers.models import BaseTrigger, TriggerContext
from core.triggers.registry import TriggerRegistry


@TriggerRegistry.register_type("time")
class TimeTrigger(BaseTrigger):
    """Fires on a cron schedule or at a specific datetime."""

    trigger_type: str = "time"

    class Conditions(BaseModel):
        """Time-based trigger conditions.

        Args:
            cron: Cron expression (e.g., '0 7 * * 1-5' for weekday 7am).
            run_at: ISO 8601 datetime for one-time fire.
        """

        cron: str | None = None
        run_at: datetime | None = None

    conditions: Conditions

    def evaluate(self, context: TriggerContext) -> bool:
        """Check if the current time matches the cron or run_at condition."""
        now = context.now

        if self.conditions.cron is not None:
            cron = croniter(self.conditions.cron, now)
            prev_fire = cron.get_prev(datetime)
            # Matches if the previous fire time is within the current second
            diff = (now - prev_fire).total_seconds()
            return 0 <= diff < 1.0

        if self.conditions.run_at is not None:
            target = self.conditions.run_at
            if target.tzinfo is None:
                target = target.replace(tzinfo=UTC)
            # Fire if we're at or past the target and haven't fired yet
            if now >= target:
                return self.last_fired is None or self.last_fired < target
            return False

        return False
```

- [ ] **Step 4: Create placeholder files for sensor and composite** (so `types/__init__.py` doesn't fail)

Create `core/triggers/types/sensor.py` and `core/triggers/types/composite.py` as stubs:

```python
# core/triggers/types/sensor.py
"""SensorTrigger — placeholder, implemented in Task 6."""
```

```python
# core/triggers/types/composite.py
"""CompositeTrigger — placeholder, implemented in Task 7."""
```

Update `types/__init__.py` to only import what exists:

```python
# core/triggers/types/__init__.py
"""Auto-import trigger types so they register via decorators."""

from core.triggers.types import time  # noqa: F401

# sensor and composite imported after implementation
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest core/triggers/tests/test_types_time.py -v`
Expected: ALL PASS

- [ ] **Step 6: Lint and type check**

Run: `uv run ruff check core/triggers/ --fix && uv run ruff format core/triggers/ && uv run mypy core/triggers/`
Expected: Clean

- [ ] **Step 7: Commit**

```bash
git add core/triggers/types/ core/triggers/tests/test_types_time.py
git commit -m "feat(triggers): add TimeTrigger with cron and run_at evaluation"
```

---

### Task 6: Concrete Trigger Types — SensorTrigger

**Files:**
- Modify: `core/triggers/types/sensor.py`
- Modify: `core/triggers/types/__init__.py`
- Test: `core/triggers/tests/test_types_sensor.py`

- [ ] **Step 1: Write failing tests**

```python
# core/triggers/tests/test_types_sensor.py
"""Tests for SensorTrigger."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bus.schemas.events import StateChangedEvent
from core.triggers.models import TriggerContext
from core.triggers.registry import TriggerRegistry


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TriggerRegistry, "_registry", {})
    import core.triggers.types.time  # noqa: F401
    import core.triggers.types.sensor  # noqa: F401


def _make_sensor_trigger(**kwargs: object) -> object:
    cls = TriggerRegistry.get("sensor")
    defaults = {
        "trigger_id": "t-1",
        "trigger_type": "sensor",
        "name": "test",
        "created_by": "test",
        "created_at": datetime.now(UTC),
        "conditions": {"entity_id": "light.living_room"},
    }
    defaults.update(kwargs)
    return cls(**defaults)


def _make_event(entity_id: str = "light.living_room", new_state: str = "on",
                attributes: dict | None = None) -> StateChangedEvent:
    return StateChangedEvent(
        source="test", domain="home", entity_id=entity_id,
        new_state=new_state, attributes=attributes or {},
    )


def test_sensor_trigger_registered() -> None:
    assert "sensor" in TriggerRegistry.available_types()


def test_entity_match() -> None:
    trigger = _make_sensor_trigger()
    ctx = TriggerContext(now=datetime.now(UTC), event=_make_event())
    assert trigger.evaluate(ctx) is True


def test_entity_no_match() -> None:
    trigger = _make_sensor_trigger()
    ctx = TriggerContext(now=datetime.now(UTC), event=_make_event(entity_id="light.bedroom"))
    assert trigger.evaluate(ctx) is False


def test_state_match() -> None:
    trigger = _make_sensor_trigger(conditions={"entity_id": "light.living_room", "state_match": "on"})
    ctx = TriggerContext(now=datetime.now(UTC), event=_make_event(new_state="on"))
    assert trigger.evaluate(ctx) is True


def test_state_no_match() -> None:
    trigger = _make_sensor_trigger(conditions={"entity_id": "light.living_room", "state_match": "on"})
    ctx = TriggerContext(now=datetime.now(UTC), event=_make_event(new_state="off"))
    assert trigger.evaluate(ctx) is False


def test_attribute_match() -> None:
    trigger = _make_sensor_trigger(
        conditions={"entity_id": "light.living_room", "attribute_match": {"brightness": 100}}
    )
    ctx = TriggerContext(now=datetime.now(UTC), event=_make_event(attributes={"brightness": 100}))
    assert trigger.evaluate(ctx) is True


def test_attribute_no_match() -> None:
    trigger = _make_sensor_trigger(
        conditions={"entity_id": "light.living_room", "attribute_match": {"brightness": 100}}
    )
    ctx = TriggerContext(now=datetime.now(UTC), event=_make_event(attributes={"brightness": 50}))
    assert trigger.evaluate(ctx) is False


def test_no_event_returns_false() -> None:
    trigger = _make_sensor_trigger()
    ctx = TriggerContext(now=datetime.now(UTC))
    assert trigger.evaluate(ctx) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest core/triggers/tests/test_types_sensor.py -v`
Expected: FAIL

- [ ] **Step 3: Implement SensorTrigger**

```python
# core/triggers/types/sensor.py
"""SensorTrigger — fires when an event matches entity/state/attribute conditions."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from core.triggers.models import BaseTrigger, TriggerContext
from core.triggers.registry import TriggerRegistry


@TriggerRegistry.register_type("sensor")
class SensorTrigger(BaseTrigger):
    """Fires when an incoming event matches conditions."""

    trigger_type: str = "sensor"

    class Conditions(BaseModel):
        """Sensor-based trigger conditions.

        Args:
            entity_id: Entity to watch (e.g., 'light.living_room').
            state_match: Optional state value to match (e.g., 'on').
            attribute_match: Optional attribute key-value pairs to match.
        """

        entity_id: str
        state_match: str | None = None
        attribute_match: dict[str, Any] | None = None

    conditions: Conditions

    def evaluate(self, context: TriggerContext) -> bool:
        """Check if the current event matches the sensor conditions."""
        if context.event is None:
            return False

        event = context.event

        if event.entity_id != self.conditions.entity_id:
            return False

        if self.conditions.state_match is not None:
            if event.new_state != self.conditions.state_match:
                return False

        if self.conditions.attribute_match is not None:
            for key, expected in self.conditions.attribute_match.items():
                if event.attributes.get(key) != expected:
                    return False

        return True
```

- [ ] **Step 4: Update `types/__init__.py`**

```python
# core/triggers/types/__init__.py
"""Auto-import trigger types so they register via decorators."""

from core.triggers.types import sensor, time  # noqa: F401

# composite imported after implementation
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest core/triggers/tests/test_types_sensor.py -v`
Expected: ALL PASS

- [ ] **Step 6: Lint and type check**

Run: `uv run ruff check core/triggers/ --fix && uv run ruff format core/triggers/ && uv run mypy core/triggers/`
Expected: Clean

- [ ] **Step 7: Commit**

```bash
git add core/triggers/types/sensor.py core/triggers/types/__init__.py core/triggers/tests/test_types_sensor.py
git commit -m "feat(triggers): add SensorTrigger with entity/state/attribute matching"
```

---

### Task 7: Concrete Trigger Types — CompositeTrigger

**Files:**
- Modify: `core/triggers/types/composite.py`
- Modify: `core/triggers/types/__init__.py`
- Test: `core/triggers/tests/test_types_composite.py`

- [ ] **Step 1: Write failing tests**

```python
# core/triggers/tests/test_types_composite.py
"""Tests for CompositeTrigger."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bus.schemas.events import StateChangedEvent
from core.triggers.models import TriggerContext
from core.triggers.registry import TriggerRegistry


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TriggerRegistry, "_registry", {})
    import core.triggers.types  # noqa: F401


def _make_composite(**kwargs: object) -> object:
    cls = TriggerRegistry.get("composite")
    defaults = {
        "trigger_id": "t-1",
        "trigger_type": "composite",
        "name": "test composite",
        "created_by": "test",
        "created_at": datetime.now(UTC),
        "conditions": {"children": [], "require": 1},
    }
    defaults.update(kwargs)
    return cls(**defaults)


def test_composite_registered() -> None:
    assert "composite" in TriggerRegistry.available_types()


def test_all_children_match() -> None:
    """Two sensor children, both match, require=2."""
    children = [
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.a", "state_match": "on"}},
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.a", "state_match": "on"}},
    ]
    trigger = _make_composite(conditions={"children": children, "require": 2})
    event = StateChangedEvent(
        source="test", domain="home", entity_id="light.a", new_state="on",
    )
    ctx = TriggerContext(now=datetime.now(UTC), event=event)
    assert trigger.evaluate(ctx) is True


def test_not_enough_children_match() -> None:
    """Two sensor children, one matches, require=2."""
    children = [
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.a", "state_match": "on"}},
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.b", "state_match": "on"}},
    ]
    trigger = _make_composite(conditions={"children": children, "require": 2})
    event = StateChangedEvent(
        source="test", domain="home", entity_id="light.a", new_state="on",
    )
    ctx = TriggerContext(now=datetime.now(UTC), event=event)
    assert trigger.evaluate(ctx) is False


def test_mixed_time_and_sensor() -> None:
    """Time child matches, sensor child matches, require=2."""
    children = [
        {"trigger_type": "time", "conditions": {"cron": "0 7 * * *"}},
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.a", "state_match": "on"}},
    ]
    trigger = _make_composite(conditions={"children": children, "require": 2})
    event = StateChangedEvent(
        source="test", domain="home", entity_id="light.a", new_state="on",
    )
    ctx = TriggerContext(now=datetime(2026, 3, 10, 7, 0, 0, tzinfo=UTC), event=event)
    assert trigger.evaluate(ctx) is True


def test_partial_require() -> None:
    """Three children, two match, require=2 → True."""
    children = [
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.a", "state_match": "on"}},
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.b", "state_match": "on"}},
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.a", "state_match": "on"}},
    ]
    trigger = _make_composite(conditions={"children": children, "require": 2})
    event = StateChangedEvent(
        source="test", domain="home", entity_id="light.a", new_state="on",
    )
    ctx = TriggerContext(now=datetime.now(UTC), event=event)
    assert trigger.evaluate(ctx) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest core/triggers/tests/test_types_composite.py -v`
Expected: FAIL

- [ ] **Step 3: Implement CompositeTrigger**

```python
# core/triggers/types/composite.py
"""CompositeTrigger — fires when N of M child conditions are met."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from core.triggers.models import BaseTrigger, TriggerContext
from core.triggers.registry import TriggerRegistry


@TriggerRegistry.register_type("composite")
class CompositeTrigger(BaseTrigger):
    """Fires when at least `require` of the child conditions evaluate to True."""

    trigger_type: str = "composite"

    class Conditions(BaseModel):
        """Composite trigger conditions.

        Args:
            children: List of child condition dicts, each with trigger_type and conditions.
            require: How many children must evaluate to True for the composite to fire.
        """

        children: list[dict[str, Any]]
        require: int

    conditions: Conditions

    def evaluate(self, context: TriggerContext) -> bool:
        """Evaluate each child trigger and check if enough are satisfied."""
        matched = 0

        for child_spec in self.conditions.children:
            child_type = child_spec.get("trigger_type", "")
            child_conditions = child_spec.get("conditions", {})

            child_cls = TriggerRegistry.get(child_type)
            # Build a minimal child trigger for evaluation only
            child = child_cls(
                trigger_id=f"{self.trigger_id}:child",
                trigger_type=child_type,
                name=f"{self.name}:child",
                created_by=self.created_by,
                created_at=self.created_at,
                conditions=child_conditions,
            )

            if child.evaluate(context):
                matched += 1
                if matched >= self.conditions.require:
                    return True

        return matched >= self.conditions.require
```

- [ ] **Step 4: Update `types/__init__.py`**

```python
# core/triggers/types/__init__.py
"""Auto-import trigger types so they register via decorators."""

from core.triggers.types import composite, sensor, time  # noqa: F401
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest core/triggers/tests/test_types_composite.py -v`
Expected: ALL PASS

- [ ] **Step 6: Run ALL trigger tests**

Run: `uv run pytest core/triggers/ -v`
Expected: ALL PASS

- [ ] **Step 7: Lint and type check**

Run: `uv run ruff check core/triggers/ --fix && uv run ruff format core/triggers/ && uv run mypy core/triggers/`
Expected: Clean

- [ ] **Step 8: Commit**

```bash
git add core/triggers/types/ core/triggers/tests/test_types_composite.py
git commit -m "feat(triggers): add CompositeTrigger with N-of-M child evaluation"
```

---

## Chunk 2: Storage, Engine, Feature, and Server

### Task 8: TriggerStore — Redis CRUD + YAML Snapshots

**Files:**
- Create: `core/triggers/store.py`
- Test: `core/triggers/tests/test_store.py`

- [ ] **Step 1: Add `core/memory/triggers/` to `.gitignore`**

Append to `.gitignore`:

```
core/memory/triggers/
```

- [ ] **Step 2: Write failing tests**

```python
# core/triggers/tests/test_store.py
"""Tests for TriggerStore — Redis CRUD + YAML snapshot/rehydration."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import yaml

from core.triggers.models import ActionPayload
from core.triggers.registry import TriggerRegistry
from core.triggers.store import TriggerStore


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TriggerRegistry, "_registry", {})
    import core.triggers.types  # noqa: F401


def _make_trigger_dict(trigger_id: str = "t-1", trigger_type: str = "time") -> dict[str, Any]:
    return {
        "trigger_id": trigger_id,
        "trigger_type": trigger_type,
        "name": "test trigger",
        "enabled": True,
        "one_shot": False,
        "created_by": "test",
        "created_at": datetime.now(UTC).isoformat(),
        "last_fired": None,
        "action": None,
        "conditions": {"cron": "0 7 * * *"} if trigger_type == "time" else {"entity_id": "light.x"},
    }


@pytest.fixture
def mock_redis() -> AsyncMock:
    r = AsyncMock()
    r.hgetall = AsyncMock(return_value={})
    r.hset = AsyncMock()
    r.hdel = AsyncMock()
    return r


@pytest.fixture
def snapshot_dir(tmp_path: Path) -> Path:
    d = tmp_path / "triggers"
    d.mkdir()
    return d


@pytest.mark.asyncio
async def test_save_writes_to_redis_and_yaml(
    mock_redis: AsyncMock, snapshot_dir: Path
) -> None:
    store = TriggerStore(redis=mock_redis, snapshot_dir=snapshot_dir)
    cls = TriggerRegistry.get("time")
    trigger = cls(**_make_trigger_dict())

    await store.save(trigger)

    mock_redis.hset.assert_called_once()
    yaml_file = snapshot_dir / "t-1.yaml"
    assert yaml_file.exists()


@pytest.mark.asyncio
async def test_delete_removes_from_redis_and_yaml(
    mock_redis: AsyncMock, snapshot_dir: Path
) -> None:
    store = TriggerStore(redis=mock_redis, snapshot_dir=snapshot_dir)
    # Pre-create a YAML file
    (snapshot_dir / "t-1.yaml").write_text("test")

    await store.delete("t-1")

    mock_redis.hdel.assert_called_once()
    assert not (snapshot_dir / "t-1.yaml").exists()


@pytest.mark.asyncio
async def test_load_from_redis(mock_redis: AsyncMock, snapshot_dir: Path) -> None:
    data = _make_trigger_dict()
    mock_redis.hgetall = AsyncMock(return_value={"t-1": json.dumps(data)})
    store = TriggerStore(redis=mock_redis, snapshot_dir=snapshot_dir)

    triggers = await store.load()

    assert len(triggers) == 1
    assert triggers[0].trigger_id == "t-1"


@pytest.mark.asyncio
async def test_load_falls_back_to_disk(
    mock_redis: AsyncMock, snapshot_dir: Path
) -> None:
    mock_redis.hgetall = AsyncMock(return_value={})
    data = _make_trigger_dict()
    (snapshot_dir / "t-1.yaml").write_text(yaml.dump(data))
    store = TriggerStore(redis=mock_redis, snapshot_dir=snapshot_dir)

    triggers = await store.load()

    assert len(triggers) == 1
    assert triggers[0].trigger_id == "t-1"
    # Should also have written back to Redis
    mock_redis.hset.assert_called()


def test_rehydrate_from_disk(snapshot_dir: Path) -> None:
    data = _make_trigger_dict()
    (snapshot_dir / "t-1.yaml").write_text(yaml.dump(data))

    triggers = TriggerStore.rehydrate_from_disk_static(snapshot_dir, TriggerRegistry)
    assert len(triggers) == 1


@pytest.mark.asyncio
async def test_list_all(mock_redis: AsyncMock, snapshot_dir: Path) -> None:
    d1 = _make_trigger_dict("t-1")
    d2 = _make_trigger_dict("t-2")
    d2["enabled"] = False
    mock_redis.hgetall = AsyncMock(return_value={
        "t-1": json.dumps(d1),
        "t-2": json.dumps(d2),
    })
    store = TriggerStore(redis=mock_redis, snapshot_dir=snapshot_dir)

    all_triggers = await store.list_all()
    assert len(all_triggers) == 2

    enabled_only = await store.list_all(enabled_only=True)
    assert len(enabled_only) == 1
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest core/triggers/tests/test_store.py -v`
Expected: FAIL — `core.triggers.store` not found

- [ ] **Step 4: Implement TriggerStore**

```python
# core/triggers/store.py
"""TriggerStore — Redis CRUD + YAML snapshot/rehydration for triggers."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from core.triggers.registry import TriggerRegistry as TriggerRegistryType

from core.triggers.models import BaseTrigger
from core.triggers.registry import TriggerRegistry

logger = logging.getLogger(__name__)

REDIS_KEY = "alfred:triggers"

# Type alias for async Redis
AioRedis = Any


class TriggerStore:
    """Redis CRUD + YAML snapshot/rehydration."""

    def __init__(self, redis: AioRedis, snapshot_dir: Path | str) -> None:
        self._redis = redis
        self._snapshot_dir = Path(snapshot_dir)
        self._snapshot_dir.mkdir(parents=True, exist_ok=True)

    async def load(self) -> list[BaseTrigger]:
        """Load all triggers from Redis, falling back to disk if empty."""
        raw: dict[str | bytes, str | bytes] = await self._redis.hgetall(REDIS_KEY)

        if raw:
            return self._parse_redis_entries(raw)

        # Fallback: rehydrate from YAML
        logger.info("Redis empty — rehydrating triggers from disk")
        triggers = self.rehydrate_from_disk_static(self._snapshot_dir, TriggerRegistry)
        # Write back to Redis for next startup
        for t in triggers:
            await self._redis.hset(REDIS_KEY, t.trigger_id, t.model_dump_json())
        return triggers

    async def save(self, trigger: BaseTrigger) -> None:
        """Write to Redis + snapshot to YAML."""
        await self._redis.hset(REDIS_KEY, trigger.trigger_id, trigger.model_dump_json())
        self._snapshot_to_yaml(trigger)

    async def delete(self, trigger_id: str) -> None:
        """Remove from Redis + delete YAML file."""
        await self._redis.hdel(REDIS_KEY, trigger_id)
        yaml_path = self._snapshot_dir / f"{trigger_id}.yaml"
        if yaml_path.exists():
            yaml_path.unlink()

    async def list_all(self, enabled_only: bool = False) -> list[BaseTrigger]:
        """Return all triggers, optionally filtered by enabled status."""
        raw: dict[str | bytes, str | bytes] = await self._redis.hgetall(REDIS_KEY)
        triggers = self._parse_redis_entries(raw)
        if enabled_only:
            return [t for t in triggers if t.enabled]
        return triggers

    async def snapshot_all(self) -> None:
        """Dump all triggers to YAML (periodic task)."""
        triggers = await self.list_all()
        for t in triggers:
            self._snapshot_to_yaml(t)

    def _snapshot_to_yaml(self, trigger: BaseTrigger) -> None:
        """Write a single trigger to YAML."""
        yaml_path = self._snapshot_dir / f"{trigger.trigger_id}.yaml"
        data = json.loads(trigger.model_dump_json())
        yaml_path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))

    def _parse_redis_entries(
        self, raw: dict[str | bytes, str | bytes]
    ) -> list[BaseTrigger]:
        """Parse raw Redis hash entries into BaseTrigger instances."""
        triggers: list[BaseTrigger] = []
        for key, value in raw.items():
            val_str = value.decode() if isinstance(value, bytes) else value
            try:
                data: dict[str, Any] = json.loads(val_str)
                trigger_type = data.get("trigger_type", "")
                cls = TriggerRegistry.get(trigger_type)
                triggers.append(cls(**data))
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                tid = key.decode() if isinstance(key, bytes) else key
                logger.error("Failed to parse trigger '%s': %s", tid, e)
        return triggers

    @staticmethod
    def rehydrate_from_disk_static(
        snapshot_dir: Path, registry: type[TriggerRegistryType]
    ) -> list[BaseTrigger]:
        """Read all YAML files and return trigger instances."""
        triggers: list[BaseTrigger] = []
        if not snapshot_dir.exists():
            return triggers

        for yaml_path in snapshot_dir.glob("*.yaml"):
            try:
                data: dict[str, Any] = yaml.safe_load(yaml_path.read_text())
                trigger_type = data.get("trigger_type", "")
                cls = registry.get(trigger_type)
                triggers.append(cls(**data))
            except Exception as e:
                logger.error("Failed to load trigger from '%s': %s", yaml_path, e)

        return triggers
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest core/triggers/tests/test_store.py -v`
Expected: ALL PASS

- [ ] **Step 6: Lint and type check**

Run: `uv run ruff check core/triggers/ --fix && uv run ruff format core/triggers/ && uv run mypy core/triggers/`
Expected: Clean

- [ ] **Step 7: Commit**

```bash
git add .gitignore core/triggers/store.py core/triggers/tests/test_store.py
git commit -m "feat(triggers): add TriggerStore with Redis CRUD and YAML snapshots"
```

---

### Task 9: TriggerEngine — Evaluation Loops and Fire Logic

**Files:**
- Create: `core/triggers/engine.py`
- Test: `core/triggers/tests/test_engine.py`

- [ ] **Step 1: Write failing tests**

```python
# core/triggers/tests/test_engine.py
"""Tests for TriggerEngine — tick loop, event listener, and fire logic."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bus.schemas.events import ActionRequest, StateChangedEvent
from core.triggers.models import ActionPayload, BaseTrigger, TriggerContext
from core.triggers.registry import TriggerRegistry


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TriggerRegistry, "_registry", {})
    import core.triggers.types  # noqa: F401


@pytest.fixture
def mock_store() -> AsyncMock:
    store = AsyncMock()
    store.save = AsyncMock()
    store.delete = AsyncMock()
    store.list_all = AsyncMock(return_value=[])
    return store


@pytest.fixture
def mock_redis() -> AsyncMock:
    r = AsyncMock()
    r.xadd = AsyncMock()
    r.lpush = AsyncMock()
    return r


@pytest.mark.asyncio
async def test_fire_with_action_publishes_action_request(
    mock_store: AsyncMock, mock_redis: AsyncMock
) -> None:
    from core.triggers.engine import TriggerEngine

    cls = TriggerRegistry.get("time")
    trigger = cls(
        trigger_id="t-1",
        trigger_type="time",
        name="test",
        created_by="test",
        created_at=datetime.now(UTC),
        conditions={"cron": "0 7 * * *"},
        action=ActionPayload(
            tool_name="smart_home.dim_lights",
            target_service="home-service",
            parameters={"room": "living_room", "level": 30},
        ),
    )

    engine = TriggerEngine(store=mock_store, redis=mock_redis)
    ctx = TriggerContext(now=datetime.now(UTC))
    await engine.fire(trigger, ctx)

    mock_redis.xadd.assert_called_once()
    call_args = mock_redis.xadd.call_args
    stream_name = call_args[0][0]
    assert stream_name == "alfred:actions"


@pytest.mark.asyncio
async def test_fire_without_action_publishes_trigger_fired(
    mock_store: AsyncMock, mock_redis: AsyncMock
) -> None:
    from core.triggers.engine import TriggerEngine

    cls = TriggerRegistry.get("time")
    trigger = cls(
        trigger_id="t-1",
        trigger_type="time",
        name="test",
        created_by="test",
        created_at=datetime.now(UTC),
        conditions={"cron": "0 7 * * *"},
    )

    engine = TriggerEngine(store=mock_store, redis=mock_redis)
    ctx = TriggerContext(now=datetime.now(UTC))
    await engine.fire(trigger, ctx)

    mock_redis.xadd.assert_called_once()
    call_args = mock_redis.xadd.call_args
    stream_name = call_args[0][0]
    assert stream_name == "alfred:events"


@pytest.mark.asyncio
async def test_fire_one_shot_deletes_trigger(
    mock_store: AsyncMock, mock_redis: AsyncMock
) -> None:
    from core.triggers.engine import TriggerEngine

    cls = TriggerRegistry.get("time")
    trigger = cls(
        trigger_id="t-1",
        trigger_type="time",
        name="test",
        created_by="test",
        created_at=datetime.now(UTC),
        conditions={"cron": "0 7 * * *"},
        one_shot=True,
    )

    engine = TriggerEngine(store=mock_store, redis=mock_redis)
    ctx = TriggerContext(now=datetime.now(UTC))
    await engine.fire(trigger, ctx)

    mock_store.delete.assert_called_once_with("t-1")


@pytest.mark.asyncio
async def test_fire_updates_last_fired(
    mock_store: AsyncMock, mock_redis: AsyncMock
) -> None:
    from core.triggers.engine import TriggerEngine

    cls = TriggerRegistry.get("time")
    trigger = cls(
        trigger_id="t-1",
        trigger_type="time",
        name="test",
        created_by="test",
        created_at=datetime.now(UTC),
        conditions={"cron": "0 7 * * *"},
        one_shot=False,
    )

    engine = TriggerEngine(store=mock_store, redis=mock_redis)
    ctx = TriggerContext(now=datetime.now(UTC))
    await engine.fire(trigger, ctx)

    # save() called to update last_fired
    mock_store.save.assert_called_once()
    saved_trigger = mock_store.save.call_args[0][0]
    assert saved_trigger.last_fired is not None


@pytest.mark.asyncio
async def test_evaluate_tick_fires_matching_time_trigger(
    mock_store: AsyncMock, mock_redis: AsyncMock
) -> None:
    from core.triggers.engine import TriggerEngine

    cls = TriggerRegistry.get("time")
    trigger = cls(
        trigger_id="t-1",
        trigger_type="time",
        name="morning",
        created_by="test",
        created_at=datetime.now(UTC),
        conditions={"cron": "0 7 * * *"},
    )
    mock_store.list_all = AsyncMock(return_value=[trigger])

    engine = TriggerEngine(store=mock_store, redis=mock_redis)
    now = datetime(2026, 3, 10, 7, 0, 0, tzinfo=UTC)
    await engine.evaluate_tick(now)

    # Should have fired (xadd called)
    mock_redis.xadd.assert_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest core/triggers/tests/test_engine.py -v`
Expected: FAIL — `core.triggers.engine` not found

- [ ] **Step 3: Implement TriggerEngine**

```python
# core/triggers/engine.py
"""TriggerEngine — evaluation loops and fire logic."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from bus.schemas.events import ActionRequest, StateChangedEvent, TriggerFired
from core.triggers.models import BaseTrigger, TriggerContext
from core.triggers.store import TriggerStore

logger = logging.getLogger(__name__)

# Type alias for async Redis
AioRedis = Any

EVENTS_STREAM = "alfred:events"
ACTIONS_STREAM = "alfred:actions"
SCRATCHPAD_QUEUE = "alfred:scratchpad:queue"


class TriggerEngine:
    """Evaluates triggers and fires actions or events."""

    def __init__(self, store: TriggerStore, redis: AioRedis) -> None:
        self._store = store
        self._redis = redis

    async def fire(self, trigger: BaseTrigger, context: TriggerContext) -> None:
        """Execute the fire logic for a trigger that evaluated to True."""
        now = datetime.now(UTC)

        if trigger.action is not None:
            action = ActionRequest(
                source="trigger-engine",
                target_service=trigger.action.target_service,
                tool_name=trigger.action.tool_name,
                parameters=trigger.action.parameters,
            )
            await self._redis.xadd(ACTIONS_STREAM, {"event": action.model_dump_json()})
            logger.info("Trigger '%s' fired → ActionRequest %s", trigger.name, action.tool_name)
        else:
            event = TriggerFired(
                trigger_id=trigger.trigger_id,
                trigger_name=trigger.name,
                trigger_type=trigger.trigger_type,
                context=self._build_fire_context(trigger, context),
            )
            await self._redis.xadd(EVENTS_STREAM, {"event": event.model_dump_json()})
            logger.info("Trigger '%s' fired → TriggerFired event", trigger.name)

        # Log to scratchpad
        observation = (
            f"{now.strftime('%Y-%m-%dT%H:%M:%SZ')} "
            f"[trigger] {trigger.name} (type={trigger.trigger_type}) fired"
        )
        await self._redis.lpush(SCRATCHPAD_QUEUE, observation)

        if trigger.one_shot:
            await self._store.delete(trigger.trigger_id)
            logger.info("One-shot trigger '%s' deleted", trigger.name)
        else:
            updated = trigger.model_copy(update={"last_fired": now})
            await self._store.save(updated)

    async def evaluate_tick(self, now: datetime) -> None:
        """Evaluate all enabled triggers against the current time (tick loop)."""
        triggers = await self._store.list_all(enabled_only=True)
        context = TriggerContext(now=now)

        for trigger in triggers:
            try:
                if trigger.evaluate(context):
                    await self.fire(trigger, context)
            except Exception as e:
                logger.error(
                    "Error evaluating trigger '%s': %s", trigger.trigger_id, e
                )

    async def evaluate_event(self, event: StateChangedEvent) -> None:
        """Evaluate all enabled triggers against an incoming event."""
        triggers = await self._store.list_all(enabled_only=True)
        now = datetime.now(UTC)
        context = TriggerContext(now=now, event=event)

        for trigger in triggers:
            try:
                if trigger.evaluate(context):
                    await self.fire(trigger, context)
            except Exception as e:
                logger.error(
                    "Error evaluating trigger '%s': %s", trigger.trigger_id, e
                )

    def _build_fire_context(
        self, trigger: BaseTrigger, context: TriggerContext
    ) -> dict[str, Any]:
        """Build the context dict for a TriggerFired event."""
        ctx: dict[str, Any] = {"trigger_type": trigger.trigger_type}
        if context.event is not None:
            ctx["event_entity"] = context.event.entity_id
            ctx["event_state"] = context.event.new_state
        ctx["evaluated_at"] = context.now.isoformat()
        return ctx
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest core/triggers/tests/test_engine.py -v`
Expected: ALL PASS

- [ ] **Step 5: Lint and type check**

Run: `uv run ruff check core/triggers/ --fix && uv run ruff format core/triggers/ && uv run mypy core/triggers/`
Expected: Clean

- [ ] **Step 6: Commit**

```bash
git add core/triggers/engine.py core/triggers/tests/test_engine.py
git commit -m "feat(triggers): add TriggerEngine with fire logic and evaluation loops"
```

---

### Task 10: TriggerFeature — CRUD Tools via BaseFeature

**Files:**
- Create: `core/triggers/feature.py`
- Test: `core/triggers/tests/test_feature.py`

- [ ] **Step 1: Write failing tests**

```python
# core/triggers/tests/test_feature.py
"""Tests for TriggerFeature — CRUD tools via BaseFeature."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from core.triggers.registry import TriggerRegistry


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TriggerRegistry, "_registry", {})
    import core.triggers.types  # noqa: F401


@pytest.fixture
def mock_store() -> AsyncMock:
    store = AsyncMock()
    store.save = AsyncMock()
    store.delete = AsyncMock()
    store.list_all = AsyncMock(return_value=[])
    return store


def test_feature_name() -> None:
    from core.triggers.feature import TriggerFeature

    f = TriggerFeature.__new__(TriggerFeature)
    assert f.feature_name == "triggers"


def test_get_tools_includes_crud() -> None:
    from core.triggers.feature import TriggerFeature

    f = TriggerFeature.__new__(TriggerFeature)
    tools = f.get_tools()
    tool_names = [t.name for t in tools]
    assert any("create_trigger" in n for n in tool_names)
    assert any("list_triggers" in n for n in tool_names)
    assert any("delete_trigger" in n for n in tool_names)
    assert any("toggle_trigger" in n for n in tool_names)
    assert any("update_trigger" in n for n in tool_names)


def test_dynamic_description_includes_trigger_types() -> None:
    from core.triggers.feature import TriggerFeature

    f = TriggerFeature.__new__(TriggerFeature)
    tools = f.get_tools()
    create_tool = next(t for t in tools if "create_trigger" in t.name)
    assert "time" in create_tool.description
    assert "sensor" in create_tool.description
    assert "composite" in create_tool.description


@pytest.mark.asyncio
async def test_create_trigger(mock_store: AsyncMock) -> None:
    from core.triggers.feature import TriggerContext_, TriggerFeature

    f = TriggerFeature(ctx=TriggerContext_(store=mock_store))
    result = await f.create_trigger(
        name="test",
        trigger_type="time",
        conditions={"cron": "0 7 * * *"},
    )
    assert result["trigger_id"]
    assert result["trigger_type"] == "time"
    mock_store.save.assert_called_once()


@pytest.mark.asyncio
async def test_create_trigger_with_action(mock_store: AsyncMock) -> None:
    from core.triggers.feature import TriggerContext_, TriggerFeature

    f = TriggerFeature(ctx=TriggerContext_(store=mock_store))
    result = await f.create_trigger(
        name="test",
        trigger_type="time",
        conditions={"cron": "0 7 * * *"},
        action={"tool_name": "x", "target_service": "y", "parameters": {}},
    )
    assert result["action"] is not None


@pytest.mark.asyncio
async def test_create_trigger_invalid_type(mock_store: AsyncMock) -> None:
    from core.triggers.feature import TriggerContext_, TriggerFeature

    f = TriggerFeature(ctx=TriggerContext_(store=mock_store))
    result = await f.create_trigger(
        name="test",
        trigger_type="nonexistent",
        conditions={},
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_delete_trigger(mock_store: AsyncMock) -> None:
    from core.triggers.feature import TriggerContext_, TriggerFeature

    f = TriggerFeature(ctx=TriggerContext_(store=mock_store))
    result = await f.delete_trigger(trigger_id="t-1")
    mock_store.delete.assert_called_once_with("t-1")
    assert result["status"] == "deleted"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest core/triggers/tests/test_feature.py -v`
Expected: FAIL — `core.triggers.feature` not found

- [ ] **Step 3: Implement TriggerFeature**

```python
# core/triggers/feature.py
"""TriggerFeature — CRUD tools for trigger management via BaseFeature."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from bus.schemas.events import TriggerCreated
from sdk.alfred_sdk.feature import BaseFeature, ToolMeta, _extract_tool_meta, tool
from core.triggers.models import ActionPayload
from core.triggers.registry import TriggerRegistry
from core.triggers.store import TriggerStore

EVENTS_STREAM = "alfred:events"


class TriggerContext_:
    """Context object passed to TriggerFeature on instantiation."""

    def __init__(self, store: TriggerStore, redis: Any = None) -> None:
        self.store = store
        self.redis = redis


class TriggerFeature(BaseFeature):
    """Manage dynamic triggers — create, list, update, delete."""

    feature_name = "triggers"

    def __init__(self, ctx: TriggerContext_ | None = None) -> None:
        if ctx is not None:
            self._store = ctx.store
            self._redis = ctx.redis
        else:
            self._store = None  # type: ignore[assignment]
            self._redis = None

    def get_tools(self) -> list[ToolMeta]:
        """Override to inject dynamic descriptions from TriggerRegistry."""
        tools = super().get_tools()
        conditions_docs = TriggerRegistry.build_conditions_docs()
        action_docs = (
            "\n\naction (optional): {tool_name: str, target_service: str, parameters: dict}\n"
            "If omitted, fires a TriggerFired event for the Reflex Engine to handle."
        )

        enriched: list[ToolMeta] = []
        for t in tools:
            if "create_trigger" in t.name:
                enriched.append(
                    ToolMeta(
                        name=t.name,
                        description=t.description + "\n\n" + conditions_docs + action_docs,
                        parameters=t.parameters,
                    )
                )
            else:
                enriched.append(t)
        return enriched

    @tool
    async def create_trigger(
        self,
        name: str,
        trigger_type: str,
        conditions: dict[str, Any],
        action: dict[str, Any] | None = None,
        one_shot: bool = False,
    ) -> dict[str, Any]:
        """Create a new trigger."""
        try:
            cls = TriggerRegistry.get(trigger_type)
        except KeyError as e:
            return {"error": str(e)}

        try:
            validated_action = ActionPayload(**action) if action else None
        except Exception as e:
            return {"error": f"Invalid action: {e}"}

        try:
            trigger = cls(
                trigger_id=str(uuid4()),
                trigger_type=trigger_type,
                name=name,
                enabled=True,
                one_shot=one_shot,
                created_by="tool-call",
                created_at=datetime.now(UTC),
                action=validated_action,
                conditions=conditions,
            )
        except Exception as e:
            return {"error": f"Invalid conditions for type '{trigger_type}': {e}"}

        await self._store.save(trigger)

        # Publish TriggerCreated event to bus
        if self._redis is not None:
            event = TriggerCreated(
                trigger_id=trigger.trigger_id,
                trigger_type=trigger_type,
                name=name,
                created_by="tool-call",
                conditions=conditions,
                action=action,
                one_shot=one_shot,
            )
            await self._redis.xadd(EVENTS_STREAM, {"event": event.model_dump_json()})

        return trigger.model_dump(mode="json")

    @tool
    async def list_triggers(self, enabled_only: bool = True) -> list[dict[str, Any]]:
        """List all triggers."""
        triggers = await self._store.list_all(enabled_only=enabled_only)
        return [t.model_dump(mode="json") for t in triggers]

    @tool
    async def update_trigger(
        self,
        trigger_id: str,
        conditions: dict[str, Any] | None = None,
        action: dict[str, Any] | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        """Update an existing trigger's conditions, action, or name."""
        triggers = await self._store.list_all()
        target = next((t for t in triggers if t.trigger_id == trigger_id), None)
        if target is None:
            return {"error": f"Trigger '{trigger_id}' not found"}

        updates: dict[str, Any] = {}
        if name is not None:
            updates["name"] = name
        if conditions is not None:
            # Re-validate conditions through the trigger's Conditions model
            cls = TriggerRegistry.get(target.trigger_type)
            try:
                conditions_model = cls.Conditions  # type: ignore[attr-defined]
                conditions_model(**conditions)
                updates["conditions"] = conditions
            except Exception as e:
                return {"error": f"Invalid conditions: {e}"}
        if action is not None:
            try:
                ActionPayload(**action)
                updates["action"] = action
            except Exception as e:
                return {"error": f"Invalid action: {e}"}

        updated = target.model_copy(update=updates)
        await self._store.save(updated)
        return updated.model_dump(mode="json")

    @tool
    async def delete_trigger(self, trigger_id: str) -> dict[str, str]:
        """Delete a trigger by ID."""
        await self._store.delete(trigger_id)
        return {"status": "deleted", "trigger_id": trigger_id}

    @tool
    async def toggle_trigger(self, trigger_id: str, enabled: bool) -> dict[str, Any]:
        """Enable or disable a trigger."""
        triggers = await self._store.list_all()
        target = next((t for t in triggers if t.trigger_id == trigger_id), None)
        if target is None:
            return {"error": f"Trigger '{trigger_id}' not found"}

        updated = target.model_copy(update={"enabled": enabled})
        await self._store.save(updated)
        return updated.model_dump(mode="json")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest core/triggers/tests/test_feature.py -v`
Expected: ALL PASS

- [ ] **Step 5: Lint and type check**

Run: `uv run ruff check core/triggers/ --fix && uv run ruff format core/triggers/ && uv run mypy core/triggers/`
Expected: Clean

- [ ] **Step 6: Commit**

```bash
git add core/triggers/feature.py core/triggers/tests/test_feature.py
git commit -m "feat(triggers): add TriggerFeature with CRUD tools and dynamic descriptions"
```

---

### Task 11: HTTP Server for Tool Dispatch

**Files:**
- Create: `core/triggers/server.py`
- Test: `core/triggers/tests/test_server.py`

Note: This follows the same JSON-RPC pattern that `home-service` uses. The Trigger Engine needs an HTTP endpoint so the Reflex Runner's action dispatcher (HomeAgent or a future generic dispatcher) can forward tool calls to it.

- [ ] **Step 1: Write failing tests**

```python
# core/triggers/tests/test_server.py
"""Tests for trigger engine HTTP server (JSON-RPC tool dispatch)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from core.triggers.registry import TriggerRegistry


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TriggerRegistry, "_registry", {})
    import core.triggers.types  # noqa: F401


@pytest.fixture
def mock_client() -> AsyncMock:
    """Mock AlfredClient with dispatch."""
    client = AsyncMock()
    client.dispatch = AsyncMock(return_value={"trigger_id": "t-1", "status": "created"})
    return client


@pytest.mark.asyncio
async def test_jsonrpc_dispatch(mock_client: AsyncMock) -> None:
    from core.triggers.server import handle_jsonrpc

    request: dict[str, Any] = {
        "method": "triggers.create_trigger",
        "params": {"name": "test", "trigger_type": "time", "conditions": {"cron": "0 7 * * *"}},
        "id": "req-1",
    }

    response = await handle_jsonrpc(request, mock_client)

    assert response["id"] == "req-1"
    assert "result" in response
    mock_client.dispatch.assert_called_once_with(
        "triggers.create_trigger",
        {"name": "test", "trigger_type": "time", "conditions": {"cron": "0 7 * * *"}},
    )


@pytest.mark.asyncio
async def test_jsonrpc_error(mock_client: AsyncMock) -> None:
    from core.triggers.server import handle_jsonrpc

    mock_client.dispatch = AsyncMock(side_effect=KeyError("Unknown tool: x"))

    request: dict[str, Any] = {
        "method": "x",
        "params": {},
        "id": "req-1",
    }

    response = await handle_jsonrpc(request, mock_client)
    assert "error" in response
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest core/triggers/tests/test_server.py -v`
Expected: FAIL

- [ ] **Step 3: Implement server**

```python
# core/triggers/server.py
"""HTTP server for trigger engine tool dispatch (JSON-RPC)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def handle_jsonrpc(
    request: dict[str, Any],
    client: Any,  # AlfredClient
) -> dict[str, Any]:
    """Handle a single JSON-RPC request by dispatching to the AlfredClient."""
    method = request.get("method", "")
    params = request.get("params", {})
    req_id = request.get("id", None)

    try:
        result = await client.dispatch(method, params)
        return {"jsonrpc": "2.0", "result": result, "id": req_id}
    except Exception as e:
        logger.error("JSON-RPC error for method '%s': %s", method, e)
        return {
            "jsonrpc": "2.0",
            "error": {"code": -32603, "message": str(e)},
            "id": req_id,
        }


async def run_server(
    client: Any,
    host: str = "0.0.0.0",
    port: int = 8001,
) -> None:
    """Run the HTTP server for trigger engine tool dispatch.

    Uses a minimal httpx-compatible server. In production this would use
    uvicorn + a small ASGI app, but for now we keep it simple with the
    stdlib or a lightweight approach.
    """
    from http.server import BaseHTTPRequestHandler
    import asyncio
    import json

    # We use aiohttp-like pattern with asyncio.start_server for simplicity
    async def handle_connection(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            # Read HTTP request
            request_line = await reader.readline()
            headers: dict[str, str] = {}
            while True:
                header_line = await reader.readline()
                if header_line in (b"\r\n", b"\n", b""):
                    break
                key, _, value = header_line.decode().partition(":")
                headers[key.strip().lower()] = value.strip()

            content_length = int(headers.get("content-length", "0"))
            body = await reader.readexactly(content_length) if content_length else b""

            rpc_request: dict[str, Any] = json.loads(body) if body else {}
            rpc_response = await handle_jsonrpc(rpc_request, client)

            response_body = json.dumps(rpc_response).encode()
            http_response = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: " + str(len(response_body)).encode() + b"\r\n"
                b"\r\n" + response_body
            )
            writer.write(http_response)
            await writer.drain()
        except Exception as e:
            logger.error("Server error: %s", e)
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle_connection, host, port)
    logger.info("Trigger Engine HTTP server listening on %s:%d", host, port)

    async with server:
        await server.serve_forever()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest core/triggers/tests/test_server.py -v`
Expected: ALL PASS

- [ ] **Step 5: Lint and type check**

Run: `uv run ruff check core/triggers/ --fix && uv run ruff format core/triggers/ && uv run mypy core/triggers/`
Expected: Clean

- [ ] **Step 6: Commit**

```bash
git add core/triggers/server.py core/triggers/tests/test_server.py
git commit -m "feat(triggers): add HTTP JSON-RPC server for tool dispatch"
```

---

### Task 12: Entry Point — `__main__.py`

**Files:**
- Create: `core/triggers/__main__.py`

- [ ] **Step 1: Implement entry point**

```python
# core/triggers/__main__.py
"""Entry point for the Trigger Engine service.

Usage: python -m core.triggers
"""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import UTC, datetime
from pathlib import Path

import redis.asyncio as aioredis

from core.triggers.engine import TriggerEngine
from core.triggers.feature import TriggerFeature
from core.triggers.store import TriggerStore
from sdk.alfred_sdk.client import AlfredClient
from shared.config import AlfredConfig

# Ensure all trigger types are registered
import core.triggers.types  # noqa: F401

logger = logging.getLogger(__name__)

EVENTS_STREAM = "alfred:events"
GROUP = "trigger-engine"
CONSUMER = "worker-1"
SNAPSHOT_DIR = Path("core/memory/triggers")

_shutdown = asyncio.Event()


def _handle_signal() -> None:
    logger.info("Shutdown signal received")
    _shutdown.set()


async def tick_loop(engine: TriggerEngine) -> None:
    """1-second tick loop for time-based trigger evaluation."""
    while not _shutdown.is_set():
        try:
            await engine.evaluate_tick(datetime.now(UTC))
        except Exception as e:
            logger.error("Tick loop error: %s", e)
        await asyncio.sleep(1.0)


async def event_loop(
    engine: TriggerEngine,
    r: aioredis.Redis,  # type: ignore[type-arg]
) -> None:
    """Event listener loop for sensor-based trigger evaluation."""
    from bus.schemas.events import StateChangedEvent

    try:
        await r.xgroup_create(EVENTS_STREAM, GROUP, id="0", mkstream=True)
    except aioredis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise

    while not _shutdown.is_set():
        try:
            entries = await r.xreadgroup(
                GROUP, CONSUMER, {EVENTS_STREAM: ">"}, count=10, block=5000
            )
        except Exception as e:
            logger.error("Event read error: %s", e)
            continue

        for _stream_key, stream_entries in entries:
            for entry_id, entry_data in stream_entries:
                raw_event = entry_data.get("event") or entry_data.get(b"event")
                if raw_event is None:
                    await r.xack(EVENTS_STREAM, GROUP, entry_id)
                    continue

                event_str = raw_event.decode() if isinstance(raw_event, bytes) else raw_event

                try:
                    event = StateChangedEvent.model_validate_json(event_str)
                except Exception:
                    # Not a StateChangedEvent — ignore (filter for state_changed only)
                    await r.xack(EVENTS_STREAM, GROUP, entry_id)
                    continue

                try:
                    await engine.evaluate_event(event)
                except Exception as e:
                    logger.error("Event evaluation error: %s", e)

                await r.xack(EVENTS_STREAM, GROUP, entry_id)


async def snapshot_loop(store: TriggerStore, interval: float = 300.0) -> None:
    """Periodic YAML snapshot (every 5 minutes)."""
    while not _shutdown.is_set():
        await asyncio.sleep(interval)
        try:
            await store.snapshot_all()
            logger.debug("Periodic trigger snapshot complete")
        except Exception as e:
            logger.error("Snapshot error: %s", e)


async def run(config: AlfredConfig) -> None:
    """Main Trigger Engine loop."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    r: aioredis.Redis = aioredis.from_url(config.redis_url)  # type: ignore[type-arg]

    store = TriggerStore(redis=r, snapshot_dir=SNAPSHOT_DIR)
    triggers = await store.load()
    logger.info("Loaded %d triggers", len(triggers))

    engine = TriggerEngine(store=store, redis=r)

    # Register CRUD tools via public AlfredClient API
    client = AlfredClient(
        service_name="trigger-engine",
        service_endpoint="http://localhost:8001",
        redis_url=config.redis_url,
    )
    from core.triggers.feature import TriggerContext_ as TriggerFeatureCtx
    ctx = TriggerFeatureCtx(store=store, redis=r)
    client.discover_features_from_classes([TriggerFeature], ctx=ctx)
    await client.register()
    logger.info("Registered trigger CRUD tools in tool registry")

    # Start concurrent tasks
    tasks = [
        asyncio.create_task(tick_loop(engine)),
        asyncio.create_task(event_loop(engine, r)),
        asyncio.create_task(snapshot_loop(store)),
    ]

    from core.triggers.server import run_server

    server_task = asyncio.create_task(run_server(client, port=8001))
    tasks.append(server_task)

    logger.info("Trigger Engine started")

    try:
        await _shutdown.wait()
    finally:
        logger.info("Shutting down Trigger Engine...")
        for t in tasks:
            t.cancel()
        await client.unregister()
        await r.aclose()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    config = AlfredConfig.from_env()
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write basic tests for the entry point**

```python
# core/triggers/tests/test_main.py
"""Tests for the Trigger Engine entry point."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.triggers.registry import TriggerRegistry


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TriggerRegistry, "_registry", {})
    import core.triggers.types  # noqa: F401


def test_main_is_importable() -> None:
    """Verify the entry point module imports without error."""
    import core.triggers.__main__  # noqa: F401


def test_main_function_exists() -> None:
    from core.triggers.__main__ import main
    assert callable(main)


@pytest.mark.asyncio
async def test_tick_loop_calls_evaluate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify tick_loop calls engine.evaluate_tick."""
    from core.triggers.__main__ import _shutdown, tick_loop

    mock_engine = AsyncMock()
    mock_engine.evaluate_tick = AsyncMock()

    # Set shutdown after first iteration
    async def fake_sleep(duration: float) -> None:
        _shutdown.set()

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    _shutdown.clear()
    await tick_loop(mock_engine)

    mock_engine.evaluate_tick.assert_called()
    _shutdown.clear()  # Reset for other tests
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `uv run pytest core/triggers/tests/test_main.py -v`
Expected: ALL PASS

- [ ] **Step 4: Lint and type check**

Run: `uv run ruff check core/triggers/ --fix && uv run ruff format core/triggers/ && uv run mypy core/triggers/`
Expected: Clean

- [ ] **Step 5: Commit**

```bash
git add core/triggers/__main__.py core/triggers/tests/test_main.py
git commit -m "feat(triggers): add __main__.py entry point for Trigger Engine service"
```

---

### Task 13: Update Documentation

**Files:**
- Modify: `core/CLAUDE.md`
- Modify: `CLAUDE.md` (root)

- [ ] **Step 1: Update `core/CLAUDE.md`** to document `triggers/` with its run command and structure

- [ ] **Step 2: Update root `CLAUDE.md`** to add `python -m core.triggers` to the running instructions, after the Reflex Runner step

- [ ] **Step 3: Commit**

```bash
git add core/CLAUDE.md CLAUDE.md
git commit -m "docs: update CLAUDE.md files with Trigger Engine run instructions"
```

---

### Task 14: Full Integration Test

- [ ] **Step 1: Run the entire test suite**

Run: `uv run pytest core/triggers/ -v --tb=short`
Expected: ALL PASS

- [ ] **Step 2: Run full project linting**

Run: `uv run ruff check . --fix && uv run ruff format .`
Expected: Clean

- [ ] **Step 3: Run full project type checking**

Run: `uv run mypy bus/ core/ domains/ sdk/ shared/ telemetry/`
Expected: Clean

- [ ] **Step 4: Run full project test suite**

Run: `uv run pytest`
Expected: ALL PASS (no regressions)

- [ ] **Step 5: Final commit if any fixes were needed**

```bash
git add -A
git commit -m "fix: resolve lint/type/test issues from Trigger Engine integration"
```
