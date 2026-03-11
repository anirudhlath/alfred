# ContextProvider & HA Entity Snapshot Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let services publish structured entity context to Redis so the Reflex Engine has full situational awareness of available entities.

**Architecture:** SDK defines a `ContextProvider` protocol and `ContextSnapshot` model. `BaseFeature` gains a default `get_context()`. `AlfredClient.register()` collects context from all features and writes it to Redis. A new `ContextReader` in the Reflex Engine fetches, caches, and renders context into the LLM prompt. Per-tool enrichment (`setup()`/`to_manifest()`) in home-service is replaced by `get_context()` overrides.

**Tech Stack:** Python 3.13+, Pydantic v2, redis.asyncio, pytest + pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-03-10-context-provider-design.md`

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `sdk/alfred_sdk/context.py` | **Create** | `ContextEntry`, `ContextSnapshot` models, `ContextProvider` protocol |
| `sdk/alfred_sdk/feature.py` | **Modify** | Add default `async get_context()` to `BaseFeature` |
| `sdk/alfred_sdk/client.py` | **Modify** | Add `_collect_context()`, update `register()` to write context to Redis |
| `shared/streams.py` | **Modify** | Add `CONTEXT_KEY_PREFIX` constant |
| `home-service/alfred_ext/features/lighting.py` | **Modify** | Add `get_context()`, remove `setup()`/`to_manifest()` |
| `home-service/alfred_ext/features/scenes.py` | **Modify** | Add `get_context()`, remove `setup()`/`to_manifest()` |
| `home-service/alfred_ext/register.py` | **Modify** | Remove `initialize_features()` and `features` export |
| `home-service/app/server.py` | **Modify** | Simplify refresh loop (remove `initialize_features()` call) |
| `core/reflex/context_reader.py` | **Create** | `ContextReader` class + `render_snapshot()` for Markdown output |
| `core/reflex/engine.py` | **Modify** | Inject rendered context into LLM prompt |
| `core/reflex/__main__.py` | **Modify** | Create `ContextReader` and pass to `ReflexEngine` |

**Test files:**

| File | Action |
|---|---|
| `sdk/tests/test_context.py` | **Create** — models + `BaseFeature.get_context()` |
| `sdk/tests/test_client_context.py` | **Create** — `register()` context collection |
| `home-service/tests/test_server.py` | **Modify** — rewrite enrichment tests to use `get_context()` |
| `core/reflex/tests/test_context_reader.py` | **Create** — reader + renderer tests |
| `core/reflex/tests/test_engine.py` | **Modify** — add context injection test |

---

## Chunk 1: SDK Models & BaseFeature

### Task 1: ContextEntry and ContextSnapshot Pydantic Models

**Files:**
- Create: `sdk/alfred_sdk/context.py`
- Create: `sdk/tests/__init__.py`
- Create: `sdk/tests/test_context.py`

- [ ] **Step 1: Write the failing test**

Create `sdk/tests/__init__.py` (empty) and `sdk/tests/test_context.py`:

```python
"""Tests for ContextProvider models."""

from __future__ import annotations

from alfred_sdk.context import ContextEntry, ContextSnapshot


def test_context_entry_defaults() -> None:
    entry = ContextEntry(entity_id="light.living_room", state="on")
    assert entry.entity_id == "light.living_room"
    assert entry.state == "on"
    assert entry.attributes == {}


def test_context_entry_with_attributes() -> None:
    entry = ContextEntry(
        entity_id="light.bedroom",
        state="on",
        attributes={"brightness": 200},
    )
    assert entry.attributes["brightness"] == 200


def test_context_snapshot_defaults() -> None:
    snap = ContextSnapshot()
    assert snap.controllable == {}
    assert snap.sensors == {}


def test_context_snapshot_round_trip() -> None:
    snap = ContextSnapshot(
        controllable={
            "light": [
                ContextEntry(entity_id="light.living_room", state="on"),
            ],
        },
        sensors={
            "sensor": [
                ContextEntry(entity_id="sensor.temperature", state="22.5"),
            ],
        },
    )
    json_str = snap.model_dump_json()
    restored = ContextSnapshot.model_validate_json(json_str)
    assert restored == snap
    assert len(restored.controllable["light"]) == 1
    assert restored.sensors["sensor"][0].state == "22.5"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && uv run pytest sdk/tests/test_context.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'alfred_sdk.context'`

- [ ] **Step 3: Write minimal implementation**

Create `sdk/alfred_sdk/context.py`:

```python
"""ContextProvider protocol and data models for service context publishing."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel


class ContextEntry(BaseModel):
    """A single entity's state snapshot."""

    entity_id: str
    state: str
    attributes: dict[str, Any] = {}


class ContextSnapshot(BaseModel):
    """Structured context from a service, grouped by domain."""

    controllable: dict[str, list[ContextEntry]] = {}
    sensors: dict[str, list[ContextEntry]] = {}


@runtime_checkable
class ContextProvider(Protocol):
    """Protocol for services/features that provide context to Alfred."""

    async def get_context(self) -> ContextSnapshot: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && uv run pytest sdk/tests/test_context.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add sdk/alfred_sdk/context.py sdk/tests/__init__.py sdk/tests/test_context.py
git commit -m "feat(sdk): add ContextProvider protocol and ContextSnapshot models"
```

### Task 2: Add CONTEXT_KEY_PREFIX to shared/streams.py

**Files:**
- Modify: `shared/streams.py:7` (append after last line)

- [ ] **Step 1: Add the constant**

Add to end of `shared/streams.py`:

```python
CONTEXT_KEY_PREFIX = "alfred:context:"
```

- [ ] **Step 2: Verify linting passes**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && uv run ruff check shared/streams.py`
Expected: clean

- [ ] **Step 3: Commit**

```bash
git add shared/streams.py
git commit -m "feat(shared): add CONTEXT_KEY_PREFIX constant"
```

### Task 3: Add default get_context() to BaseFeature

**Files:**
- Modify: `sdk/alfred_sdk/feature.py:157-202` (BaseFeature class)
- Modify: `sdk/tests/test_context.py` (add test)

- [ ] **Step 1: Write the failing test**

Append to `sdk/tests/test_context.py`:

```python
import pytest

from alfred_sdk.context import ContextProvider, ContextSnapshot
from alfred_sdk.feature import BaseFeature


class StubFeature(BaseFeature):
    feature_name = "stub"


@pytest.mark.asyncio
async def test_base_feature_default_get_context() -> None:
    feature = StubFeature()
    result = await feature.get_context()
    assert result == ContextSnapshot()
    assert result.controllable == {}
    assert result.sensors == {}


def test_base_feature_satisfies_context_provider_protocol() -> None:
    feature = StubFeature()
    assert isinstance(feature, ContextProvider)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && uv run pytest sdk/tests/test_context.py::test_base_feature_default_get_context -v`
Expected: FAIL with `AttributeError: 'StubFeature' object has no attribute 'get_context'`

- [ ] **Step 3: Write minimal implementation**

In `sdk/alfred_sdk/feature.py`, add import at the top (after existing imports, around line 10):

```python
from .context import ContextSnapshot
```

Add method to `BaseFeature` class (after `to_manifest()`, around line 202):

```python
    async def get_context(self) -> ContextSnapshot:
        """Return structured context for this feature. Override in subclasses."""
        return ContextSnapshot()
```

- [ ] **Step 4: Run all SDK tests to verify they pass**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && uv run pytest sdk/tests/test_context.py -v`
Expected: 6 PASSED

- [ ] **Step 5: Run ruff + mypy on SDK**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && uv run ruff check sdk/ && uv run ruff format sdk/ && uv run mypy sdk/`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add sdk/alfred_sdk/feature.py sdk/tests/test_context.py
git commit -m "feat(sdk): add default get_context() to BaseFeature"
```

---

## Chunk 2: SDK Client Context Collection

### Task 4: AlfredClient collects and writes context during register()

**Files:**
- Modify: `sdk/alfred_sdk/client.py:133-156` (add _collect_context before register, update register)
- Create: `sdk/tests/test_client_context.py`

- [ ] **Step 1: Write the failing test**

Create `sdk/tests/test_client_context.py`:

```python
"""Tests for AlfredClient context collection during register()."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from alfred_sdk.client import AlfredClient
from alfred_sdk.context import ContextEntry, ContextSnapshot
from alfred_sdk.feature import BaseFeature, tool


class FakeFeature(BaseFeature):
    """Test feature that returns context."""

    feature_name = "fake"

    def __init__(self) -> None:
        super().__init__()

    async def get_context(self) -> ContextSnapshot:
        return ContextSnapshot(
            controllable={
                "light": [
                    ContextEntry(entity_id="light.kitchen", state="on"),
                ],
            },
        )

    @tool
    def do_thing(self, x: str) -> dict[str, Any]:
        """Do a thing.

        Args:
            x: The thing to do.
        """
        return {"x": x}


class EmptyFeature(BaseFeature):
    """Feature with no context."""

    feature_name = "empty"

    def __init__(self) -> None:
        super().__init__()

    @tool
    def noop(self) -> dict[str, Any]:
        """Do nothing."""
        return {}


@pytest.mark.asyncio
async def test_register_writes_context_to_redis() -> None:
    mock_redis = AsyncMock()
    mock_redis.hset = AsyncMock()
    mock_redis.set = AsyncMock()
    mock_redis.aclose = AsyncMock()

    client = AlfredClient(service_name="test-service")
    client.discover_features_from_classes([FakeFeature])

    with patch("redis.asyncio.from_url", return_value=mock_redis):
        await client.register()

    # Tool manifest written
    mock_redis.hset.assert_called_once()

    # Context written with TTL
    mock_redis.set.assert_called_once()
    call_args = mock_redis.set.call_args
    assert call_args[0][0] == "alfred:context:test-service"
    assert call_args[1]["ex"] == 600

    # Verify the written snapshot
    written_json = call_args[0][1]
    snapshot = ContextSnapshot.model_validate_json(written_json)
    assert "light" in snapshot.controllable
    assert snapshot.controllable["light"][0].entity_id == "light.kitchen"


@pytest.mark.asyncio
async def test_register_skips_context_when_empty() -> None:
    mock_redis = AsyncMock()
    mock_redis.hset = AsyncMock()
    mock_redis.set = AsyncMock()
    mock_redis.aclose = AsyncMock()

    client = AlfredClient(service_name="test-service")
    client.discover_features_from_classes([EmptyFeature])

    with patch("redis.asyncio.from_url", return_value=mock_redis):
        await client.register()

    # Tool manifest written, but no context
    mock_redis.hset.assert_called_once()
    mock_redis.set.assert_not_called()


@pytest.mark.asyncio
async def test_collect_context_merges_multiple_features() -> None:
    client = AlfredClient(service_name="test-service")
    client.discover_features_from_classes([FakeFeature, EmptyFeature])

    snapshot = await client._collect_context()
    assert "light" in snapshot.controllable
    assert len(snapshot.controllable["light"]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && uv run pytest sdk/tests/test_client_context.py -v`
Expected: FAIL with `AttributeError: 'AlfredClient' object has no attribute '_collect_context'`

- [ ] **Step 3: Write minimal implementation**

In `sdk/alfred_sdk/client.py`, add import at top (around line 9):

```python
from .context import ContextSnapshot
```

Add `_collect_context` method and constant before `register()` (around line 133):

```python
    # Duplicated from shared.streams — SDK must be standalone (no monorepo imports)
    CONTEXT_KEY_PREFIX = "alfred:context:"

    async def _collect_context(self) -> ContextSnapshot:
        """Collect and merge context from all registered features."""
        merged = ContextSnapshot()
        for feature in self._features:
            snapshot = await feature.get_context()
            for domain, entries in snapshot.controllable.items():
                merged.controllable.setdefault(domain, []).extend(entries)
            for domain, entries in snapshot.sensors.items():
                merged.sensors.setdefault(domain, []).extend(entries)
        return merged
```

Replace the `register()` method body:

```python
    async def register(self) -> None:
        """Register this service's tools and context with Alfred's registry on Redis."""
        import json

        import redis.asyncio as aioredis

        r: aioredis.Redis[Any] = aioredis.from_url(self.redis_url)  # type: ignore[type-arg]
        try:
            manifest = self.get_registration_manifest()
            await r.hset(self.REGISTRY_KEY, self.service_name, json.dumps(manifest))  # type: ignore[misc]

            context = await self._collect_context()
            if context.controllable or context.sensors:
                context_key = f"{self.CONTEXT_KEY_PREFIX}{self.service_name}"
                await r.set(context_key, context.model_dump_json(), ex=600)  # type: ignore[misc]
        finally:
            await r.aclose()
```

- [ ] **Step 4: Run all SDK tests to verify they pass**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && uv run pytest sdk/tests/ -v`
Expected: 9 PASSED

- [ ] **Step 5: Run ruff + mypy on SDK**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && uv run ruff check sdk/ && uv run ruff format sdk/ && uv run mypy sdk/`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add sdk/alfred_sdk/client.py sdk/tests/test_client_context.py
git commit -m "feat(sdk): collect and write context during register()"
```

---

## Chunk 3: home-service Feature Rewrite

### Task 5a: Replace setup()/to_manifest() with get_context() in LightingFeature

**Files:**
- Modify: `home-service/alfred_ext/features/lighting.py` (rewrite enrichment)
- Modify: `home-service/tests/test_server.py:68-87` (replace `test_setup_injects_available_rooms_into_manifest`)

- [ ] **Step 1: Write the failing test**

In `home-service/tests/test_server.py`, replace `test_setup_injects_available_rooms_into_manifest` (lines 68-87) with:

```python
@pytest.mark.asyncio
async def test_lighting_get_context_returns_light_entities() -> None:
    """LightingFeature.get_context() returns structured light entity data."""
    mock_ha = AsyncMock()
    mock_ha.get_states = AsyncMock(return_value=HA_STATES)

    class Ctx:
        ha = mock_ha

    from alfred_ext.features.lighting import LightingFeature

    feature = LightingFeature(Ctx())
    context = await feature.get_context()

    assert "light" in context.controllable
    entity_ids = [e.entity_id for e in context.controllable["light"]]
    assert "light.living_room" in entity_ids
    assert "light.bedroom" in entity_ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/home-service && uv run pytest tests/test_server.py::test_lighting_get_context_returns_light_entities -v`
Expected: FAIL with `AttributeError: 'LightingFeature' object has no attribute 'get_context'`

- [ ] **Step 3: Implement LightingFeature.get_context()**

Rewrite `home-service/alfred_ext/features/lighting.py`:

```python
"""Lighting feature — controls smart home lights via Home Assistant."""

from __future__ import annotations

import logging
from typing import Any

from alfred_sdk import BaseFeature, tool
from alfred_sdk.context import ContextEntry, ContextSnapshot

logger = logging.getLogger(__name__)


class LightingFeature(BaseFeature):
    """Smart home lighting controls."""

    feature_name = "lighting"

    def __init__(self, ctx: Any) -> None:
        super().__init__()
        self.ha = ctx.ha

    async def get_context(self) -> ContextSnapshot:
        """Return current state of all light entities from HA."""
        try:
            states = await self.ha.get_states()
            entries = [
                ContextEntry(
                    entity_id=s["entity_id"],
                    state=s.get("state", "unknown"),
                    attributes=s.get("attributes", {}),
                )
                for s in states
                if s["entity_id"].startswith("light.")
            ]
            return ContextSnapshot(controllable={"light": entries})
        except Exception as e:
            logger.warning("Could not query HA for light context: %s", e)
            return ContextSnapshot()

    @tool
    async def dim_lights(self, room: str, level: int) -> dict[str, Any]:
        """Dim the lights in a room.

        Args:
            room: The room name.
            level: Brightness level 0-100.
        """
        entity_id = f"light.{room.replace(' ', '_').lower()}"
        brightness = int(level * 2.55)  # Convert 0-100 to 0-255
        await self.ha.call_service(
            "light", "turn_on", {"entity_id": entity_id, "brightness": brightness}
        )
        return {"entity_id": entity_id, "brightness": level}

    @tool
    async def turn_off_lights(self, room: str) -> dict[str, Any]:
        """Turn off all lights in a room.

        Args:
            room: The room name.
        """
        entity_id = f"light.{room.replace(' ', '_').lower()}"
        await self.ha.call_service("light", "turn_off", {"entity_id": entity_id})
        return {"entity_id": entity_id, "state": "off"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/anirudhlath/code/private/alfred/home-service && uv run pytest tests/test_server.py::test_lighting_get_context_returns_light_entities -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/home-service
git add alfred_ext/features/lighting.py tests/test_server.py
git commit -m "feat(lighting): replace setup()/to_manifest() with get_context()"
```

### Task 5b: Replace setup()/to_manifest() with get_context() in SceneFeature

**Files:**
- Modify: `home-service/alfred_ext/features/scenes.py` (rewrite enrichment)
- Modify: `home-service/tests/test_server.py:90-107` (replace `test_setup_injects_available_scenes_into_manifest`)

- [ ] **Step 1: Write the failing test**

In `home-service/tests/test_server.py`, replace `test_setup_injects_available_scenes_into_manifest` with:

```python
@pytest.mark.asyncio
async def test_scenes_get_context_returns_scene_entities() -> None:
    """SceneFeature.get_context() returns structured scene entity data."""
    mock_ha = AsyncMock()
    mock_ha.get_states = AsyncMock(return_value=HA_STATES)

    class Ctx:
        ha = mock_ha

    from alfred_ext.features.scenes import SceneFeature

    feature = SceneFeature(Ctx())
    context = await feature.get_context()

    assert "scene" in context.controllable
    entity_ids = [e.entity_id for e in context.controllable["scene"]]
    assert "scene.movie_night" in entity_ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/home-service && uv run pytest tests/test_server.py::test_scenes_get_context_returns_scene_entities -v`
Expected: FAIL with `AttributeError: 'SceneFeature' object has no attribute 'get_context'`

- [ ] **Step 3: Implement SceneFeature.get_context()**

Rewrite `home-service/alfred_ext/features/scenes.py`:

```python
"""Scene feature — activates Home Assistant scenes."""

from __future__ import annotations

import logging
from typing import Any

from alfred_sdk import BaseFeature, tool
from alfred_sdk.context import ContextEntry, ContextSnapshot

logger = logging.getLogger(__name__)


class SceneFeature(BaseFeature):
    """Smart home scene management."""

    feature_name = "scenes"

    def __init__(self, ctx: Any) -> None:
        super().__init__()
        self.ha = ctx.ha

    async def get_context(self) -> ContextSnapshot:
        """Return current state of all scene entities from HA."""
        try:
            states = await self.ha.get_states()
            entries = [
                ContextEntry(
                    entity_id=s["entity_id"],
                    state=s.get("state", "unknown"),
                    attributes=s.get("attributes", {}),
                )
                for s in states
                if s["entity_id"].startswith("scene.")
            ]
            return ContextSnapshot(controllable={"scene": entries})
        except Exception as e:
            logger.warning("Could not query HA for scene context: %s", e)
            return ContextSnapshot()

    @tool
    async def set_scene(self, scene_name: str) -> dict[str, Any]:
        """Activate a Home Assistant scene.

        Args:
            scene_name: The scene to activate.
        """
        entity_id = f"scene.{scene_name.replace(' ', '_').lower()}"
        await self.ha.call_service("scene", "turn_on", {"entity_id": entity_id})
        return {"scene": scene_name, "activated": True}
```

- [ ] **Step 4: Run all tests to verify they pass**

Run: `cd /Users/anirudhlath/code/private/alfred/home-service && uv run pytest tests/test_server.py -v`
Expected: All PASSED

- [ ] **Step 5: Run ruff + mypy**

Run: `cd /Users/anirudhlath/code/private/alfred/home-service && uv run ruff check . && uv run ruff format . && uv run mypy alfred_ext/`
Expected: clean

- [ ] **Step 6: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/home-service
git add alfred_ext/features/scenes.py tests/test_server.py
git commit -m "feat(scenes): replace setup()/to_manifest() with get_context()"
```

### Task 6: Remove initialize_features() and simplify server.py

**Files:**
- Modify: `home-service/alfred_ext/register.py:44-55` (remove features export + initialize_features)
- Modify: `home-service/app/server.py:38-72` (simplify lifespan)

- [ ] **Step 1: Simplify register.py**

Rewrite `home-service/alfred_ext/register.py`:

```python
"""Alfred integration for home-service.

Optional — this module is only used when alfred-sdk is installed.
The home-service works independently without it.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from alfred_sdk import AlfredClient  # noqa: E402

from app.ha_client import HomeAssistantClient  # noqa: E402

logger = logging.getLogger(__name__)

ha = HomeAssistantClient(
    host=os.getenv("HA_HOST", "http://homeassistant.local:8123"),
    token=os.getenv("HA_TOKEN", ""),
)

client = AlfredClient(
    service_name="home-service",
    service_endpoint=f"http://{os.getenv('SERVICE_HOST', 'localhost')}:8000/mcp",
)


class HomeServiceContext:
    """Shared dependencies for all home-service features."""

    def __init__(self, ha: HomeAssistantClient) -> None:
        self.ha = ha


import alfred_ext.features as features_pkg  # noqa: E402

client.discover_features(
    package=features_pkg,
    ctx=HomeServiceContext(ha=ha),
)
```

- [ ] **Step 2: Simplify server.py lifespan**

Rewrite the `lifespan` function in `home-service/app/server.py`:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    """Register tools and context with Alfred on startup, unregister on shutdown."""
    refresh_task: asyncio.Task[None] | None = None
    try:
        from alfred_ext.register import client

        await client.register()
        logger.info("Registered tools and context with Alfred registry")

        async def _refresh_loop() -> None:
            """Re-register periodically (refreshes tools + context)."""
            while True:
                await asyncio.sleep(ENTITY_REFRESH_INTERVAL)
                try:
                    await client.register()
                    logger.debug("Refreshed tool registry and context")
                except Exception as e:
                    logger.warning("Registration refresh failed: %s", e)

        refresh_task = asyncio.create_task(_refresh_loop())
    except Exception as e:
        logger.warning("Could not register with Alfred: %s", e)
    yield
    if refresh_task is not None:
        refresh_task.cancel()
    try:
        from alfred_ext.register import client

        await client.unregister()
        logger.info("Unregistered from Alfred registry")
    except Exception as e:
        logger.warning("Could not unregister from Alfred: %s", e)
```

- [ ] **Step 3: Run all home-service tests**

Run: `cd /Users/anirudhlath/code/private/alfred/home-service && uv run pytest tests/ -v`
Expected: All PASSED

- [ ] **Step 4: Run ruff + mypy**

Run: `cd /Users/anirudhlath/code/private/alfred/home-service && uv run ruff check . && uv run ruff format .`
Expected: clean

- [ ] **Step 5: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/home-service
git add alfred_ext/register.py app/server.py
git commit -m "refactor: remove initialize_features(), simplify server refresh loop"
```

---

## Chunk 4: Reflex Engine Consumer

### Task 7: Context reader with rendering and caching

**Files:**
- Create: `core/reflex/context_reader.py`
- Create: `core/reflex/tests/test_context_reader.py`

- [ ] **Step 1: Write the failing tests**

Create `core/reflex/tests/test_context_reader.py`:

```python
"""Tests for the context reader — Redis fetch, cache, and Markdown rendering."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from sdk.alfred_sdk.context import ContextEntry, ContextSnapshot


def _make_snapshot() -> ContextSnapshot:
    return ContextSnapshot(
        controllable={
            "light": [
                ContextEntry(
                    entity_id="light.living_room",
                    state="on",
                    attributes={"brightness": 255},
                ),
                ContextEntry(entity_id="light.bedroom", state="off"),
            ],
            "scene": [
                ContextEntry(entity_id="scene.movie_night", state="scening"),
            ],
        },
        sensors={
            "sensor": [
                ContextEntry(entity_id="sensor.temperature", state="22.5"),
            ],
        },
    )


def test_render_snapshot_produces_markdown() -> None:
    from core.reflex.context_reader import render_snapshot

    snapshot = _make_snapshot()
    result = render_snapshot(snapshot)

    assert "### Lights" in result
    assert "- light.living_room: on (brightness: 255)" in result
    assert "- light.bedroom: off" in result
    assert "### Scenes" in result
    assert "- scene.movie_night: scening" in result
    assert "### Sensors" in result
    assert "- sensor.temperature: 22.5" in result


def test_render_empty_snapshot() -> None:
    from core.reflex.context_reader import render_snapshot

    result = render_snapshot(ContextSnapshot())
    assert result == ""


@pytest.mark.asyncio
async def test_context_reader_fetches_from_redis() -> None:
    from core.reflex.context_reader import ContextReader

    snapshot = _make_snapshot()

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=snapshot.model_dump_json().encode())
    mock_redis.aclose = AsyncMock()

    with patch("redis.asyncio.from_url", return_value=mock_redis):
        reader = ContextReader(redis_url="redis://localhost:6379")
        result = await reader.get_rendered_context()

    assert "light.living_room" in result
    assert "brightness: 255" in result
    mock_redis.get.assert_called_once_with("alfred:context:home-service")


@pytest.mark.asyncio
async def test_context_reader_caches_result() -> None:
    from core.reflex.context_reader import ContextReader

    snapshot = _make_snapshot()

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=snapshot.model_dump_json().encode())
    mock_redis.aclose = AsyncMock()

    with patch("redis.asyncio.from_url", return_value=mock_redis):
        reader = ContextReader(redis_url="redis://localhost:6379")
        result1 = await reader.get_rendered_context()
        result2 = await reader.get_rendered_context()

    assert result1 == result2
    # Redis only queried once (cached)
    mock_redis.get.assert_called_once()


@pytest.mark.asyncio
async def test_context_reader_returns_empty_when_key_missing() -> None:
    from core.reflex.context_reader import ContextReader

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.aclose = AsyncMock()

    with patch("redis.asyncio.from_url", return_value=mock_redis):
        reader = ContextReader(redis_url="redis://localhost:6379")
        result = await reader.get_rendered_context()

    assert result == ""


@pytest.mark.asyncio
async def test_context_reader_caches_empty_result() -> None:
    """Empty result (key missing) should also be cached — don't re-query Redis."""
    from core.reflex.context_reader import ContextReader

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.aclose = AsyncMock()

    with patch("redis.asyncio.from_url", return_value=mock_redis):
        reader = ContextReader(redis_url="redis://localhost:6379")
        await reader.get_rendered_context()
        await reader.get_rendered_context()

    # Redis only queried once despite empty result
    mock_redis.get.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && uv run pytest core/reflex/tests/test_context_reader.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.reflex.context_reader'`

- [ ] **Step 3: Write minimal implementation**

Create `core/reflex/context_reader.py`:

```python
"""Context reader — fetches and renders service context from Redis."""

from __future__ import annotations

import logging
import time
from typing import Any

from sdk.alfred_sdk.context import ContextEntry, ContextSnapshot
from shared.streams import CONTEXT_KEY_PREFIX

logger = logging.getLogger(__name__)


def render_snapshot(snapshot: ContextSnapshot) -> str:
    """Render a ContextSnapshot into Markdown for the LLM prompt."""
    lines: list[str] = []

    for domain, entries in sorted(snapshot.controllable.items()):
        title = domain.replace("_", " ").title() + "s"
        lines.append(f"### {title}")
        for e in entries:
            attrs = ""
            if e.attributes:
                attr_parts = [f"{k}: {v}" for k, v in e.attributes.items()]
                attrs = f" ({', '.join(attr_parts)})"
            lines.append(f"- {e.entity_id}: {e.state}{attrs}")
        lines.append("")

    for domain, entries in sorted(snapshot.sensors.items()):
        title = domain.replace("_", " ").title() + "s"
        lines.append(f"### {title}")
        for e in entries:
            lines.append(f"- {e.entity_id}: {e.state}")
        lines.append("")

    return "\n".join(lines).rstrip()


class ContextReader:
    """Reads and caches service context from Redis."""

    CACHE_TTL = 300.0  # 5 minutes

    def __init__(self, redis_url: str, service_name: str = "home-service") -> None:
        self._redis_url = redis_url
        self._service_name = service_name
        self._cached_rendered: str = ""
        self._cache_time: float = 0.0
        self._cache_valid: bool = False

    async def get_rendered_context(self) -> str:
        """Return rendered Markdown context, re-fetching after TTL."""
        now = time.monotonic()
        if not self._cache_valid or (now - self._cache_time) > self.CACHE_TTL:
            import redis.asyncio as aioredis

            r: aioredis.Redis[Any] = aioredis.from_url(self._redis_url)  # type: ignore[type-arg]
            try:
                key = f"{CONTEXT_KEY_PREFIX}{self._service_name}"
                raw: bytes | None = await r.get(key)  # type: ignore[misc]
                if raw:
                    snapshot = ContextSnapshot.model_validate_json(raw)
                    self._cached_rendered = render_snapshot(snapshot)
                else:
                    self._cached_rendered = ""
            finally:
                await r.aclose()
            self._cache_time = now
            self._cache_valid = True

        return self._cached_rendered
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && uv run pytest core/reflex/tests/test_context_reader.py -v`
Expected: 6 PASSED

- [ ] **Step 5: Run ruff + mypy**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && uv run ruff check core/reflex/context_reader.py && uv run mypy core/reflex/context_reader.py`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add core/reflex/context_reader.py core/reflex/tests/test_context_reader.py
git commit -m "feat(reflex): add ContextReader with TTL cache and Markdown rendering"
```

### Task 8a: Inject context into Reflex Engine prompt

**Files:**
- Modify: `core/reflex/engine.py:69-75,103-122` (add ContextReader to init + prompt)
- Modify: `core/reflex/tests/test_engine.py` (add context injection test)

- [ ] **Step 1: Write the failing test**

Append to `core/reflex/tests/test_engine.py`:

```python
@pytest.mark.asyncio
async def test_reflex_engine_prompt_contains_context(
    mock_registry: AsyncMock,
    mock_preferences: str,
) -> None:
    from unittest.mock import AsyncMock as AM

    from core.reflex.engine import ReflexEngine

    mock_context_reader = AM()
    mock_context_reader.get_rendered_context = AM(
        return_value="### Lights\n- light.living_room: on (brightness: 255)"
    )

    with patch("core.reflex.memory_reader.read_preferences", return_value=mock_preferences):
        engine = ReflexEngine(
            preferences_dir="/fake/prefs",
            tool_registry=mock_registry,
            context_reader=mock_context_reader,
        )

    boring_event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="sensor.temperature",
        new_state="22.5",
    )

    mock_ollama_response = {
        "response": json.dumps({"action": "none"}),
        "prompt_tokens": 150,
        "completion_tokens": 10,
        "total_tokens": 160,
    }

    with (
        patch("core.reflex.memory_reader.read_preferences", return_value=mock_preferences),
        patch(
            "core.reflex.ollama_client.infer",
            new_callable=AsyncMock,
            return_value=mock_ollama_response,
        ) as mock_infer,
    ):
        await engine.process_event(boring_event)

    # Verify the prompt sent to Ollama contains the rendered context
    called_prompt = mock_infer.call_args[0][0]
    assert "## Home State" in called_prompt
    assert "light.living_room: on (brightness: 255)" in called_prompt
    # Context should appear before preferences
    assert called_prompt.index("## Home State") < called_prompt.index("## User Preferences")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && uv run pytest core/reflex/tests/test_engine.py::test_reflex_engine_prompt_contains_context -v`
Expected: FAIL with `TypeError: ReflexEngine.__init__() got an unexpected keyword argument 'context_reader'`

- [ ] **Step 3: Modify ReflexEngine.__init__() to accept ContextReader**

In `core/reflex/engine.py`, add import at top (after line 15):

```python
from core.reflex.context_reader import ContextReader
```

Modify `__init__` signature (line 69):

```python
    def __init__(
        self,
        preferences_dir: str,
        tool_registry: ToolRegistry,
        context_reader: ContextReader | None = None,
    ) -> None:
        self.preferences_dir = preferences_dir
        self._registry = tool_registry
        self._context_reader = context_reader
        self._cached_preferences: str | None = None
        self._cached_tools: list[ToolInfo] | None = None
        self._cached_system_prompt: str | None = None
        self._cache_time: float = 0.0
```

- [ ] **Step 4: Modify process_event() to inject context**

Replace the prompt-building section in `process_event()` (lines 106-119):

```python
    @track_latency(category="reflex")
    async def process_event(self, event: StateChangedEvent) -> ActionRequest | None:
        """Process a state change event and optionally produce an action."""
        preferences = self._get_preferences()
        tools, system_prompt = await self._get_tools_and_prompt()
        valid_services = ToolRegistry.get_registered_services(tools)

        context = ""
        if self._context_reader is not None:
            context = await self._context_reader.get_rendered_context()

        context_section = f"## Home State\n{context}\n\n" if context else ""

        prompt = (
            f"{system_prompt}\n\n"
            f"{context_section}"
            f"## User Preferences\n{preferences}\n\n"
            f"## Event\n"
            f"Entity: {event.entity_id}\n"
            f"Domain: {event.domain}\n"
            f"Changed: {event.old_state} → {event.new_state}\n"
            f"Attributes: {json.dumps(event.attributes)}\n\n"
            f"## Your Decision (JSON only):"
        )

        response = await ollama_client.infer(prompt)
        return self._parse_response(response, event, valid_services)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && uv run pytest core/reflex/tests/test_engine.py -v`
Expected: 5 PASSED (4 existing + 1 new)

- [ ] **Step 6: Run ruff + mypy**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && uv run ruff check core/reflex/engine.py && uv run mypy core/reflex/engine.py`
Expected: clean

- [ ] **Step 7: Commit**

```bash
git add core/reflex/engine.py core/reflex/tests/test_engine.py
git commit -m "feat(reflex): inject service context into LLM prompt"
```

### Task 8b: Wire ContextReader in Reflex Runner entry point

**Files:**
- Modify: `core/reflex/__main__.py:76` (create ContextReader and pass to engine)

- [ ] **Step 1: Add import and create ContextReader**

In `core/reflex/__main__.py`, add import (after line 15):

```python
from core.reflex.context_reader import ContextReader
```

Modify line 76 to pass context_reader:

```python
    context_reader = ContextReader(redis_url=config.redis_url)
    engine = ReflexEngine(
        preferences_dir="core/memory/preferences",
        tool_registry=registry,
        context_reader=context_reader,
    )
```

- [ ] **Step 2: Run full test suite**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && uv run pytest -v`
Expected: All PASSED

- [ ] **Step 3: Run ruff + mypy**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && uv run ruff check . --fix && uv run ruff format . && uv run mypy core/ sdk/ shared/`
Expected: clean

- [ ] **Step 4: Commit**

```bash
git add core/reflex/__main__.py
git commit -m "feat(reflex): wire ContextReader into Reflex Runner"
```

---

## Post-Implementation

- [ ] **Run full test suite across both repos**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred && uv run pytest -v
cd /Users/anirudhlath/code/private/alfred/home-service && uv run pytest -v
```

- [ ] **Add backlog items**

Add to `docs/backlog/`:
- Agent-scoped context visibility (replace hardcoded `home-service` key)
- Option C entities (system entities, automations, scripts)

- [ ] **Update CLAUDE.md**

Add `core/reflex/context_reader.py` and `sdk/alfred_sdk/context.py` to Key Paths section.
