# Trigger Engine Hardening — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate per-tick Redis round-trips, cache model-level objects, and replace the raw asyncio HTTP server with FastAPI/uvicorn.

**Architecture:** Write-through in-memory cache in `TriggerStore` serves all reads; CRUD writes update both Redis and cache. `CompositeTrigger` pre-builds child instances at construction via `model_post_init`. `TimeTrigger` validates cron at construction. Raw asyncio HTTP server replaced by FastAPI with REST + JSON-RPC backward-compat shim.

**Tech Stack:** Python 3.13+, Pydantic v2 (`PrivateAttr`, `model_post_init`), FastAPI, uvicorn, pytest, pytest-asyncio, croniter

**Spec:** `docs/superpowers/specs/2026-03-17-trigger-engine-hardening-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `core/triggers/store.py` | Modify | Add `_cache` dict, write-through on save/delete, cache-read on list_all/get, new `refresh()` |
| `core/triggers/types/composite.py` | Modify | Add `PrivateAttr` + `model_post_init` to pre-build children, indexed child IDs |
| `core/triggers/types/time.py` | Modify | Add `PrivateAttr` + `model_post_init` to validate cron at construction |
| `core/triggers/server.py` | Rewrite | FastAPI app: REST endpoints + JSON-RPC shim + health check |
| `core/triggers/__main__.py` | Modify | Add `refresh_loop`, uvicorn programmatic server, graceful shutdown |
| `pyproject.toml` | Modify | Add `fastapi>=0.115`, `uvicorn[standard]>=0.34` |
| `core/triggers/tests/test_store.py` | Modify | Update `test_list_all` to call `load()` first; add cache tests |
| `core/triggers/tests/test_server.py` | Rewrite | FastAPI `TestClient`-based tests for REST + JSON-RPC shim |
| `core/triggers/tests/test_types_composite.py` | Modify | Add tests for `_cached_children` and indexed child IDs |
| `core/triggers/tests/test_types_time.py` | Modify | Add test for `_cached_cron` validation at construction |

---

## Task 1: Add Dependencies

**Files:**
- Modify: `pyproject.toml:6-17`

- [ ] **Step 1: Add fastapi and uvicorn to pyproject.toml**

```python
# In dependencies list, add after "pyyaml>=6.0":
    "fastapi>=0.115",
    "uvicorn[standard]>=0.34",
```

Also add to `[tool.mypy]` overrides since uvicorn has no inline types:

```toml
[[tool.mypy.overrides]]
module = ["uvicorn.*"]
ignore_missing_imports = true
```

- [ ] **Step 2: Install and verify**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && uv pip install -e ".[dev]"`
Expected: Installs successfully with fastapi and uvicorn available.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "build: add fastapi and uvicorn dependencies for trigger engine server"
```

---

## Task 2: In-Memory Trigger Cache in TriggerStore

**Files:**
- Modify: `core/triggers/store.py`
- Modify: `core/triggers/tests/test_store.py`

- [ ] **Step 1: Write failing tests for cache behavior**

Add these tests to `core/triggers/tests/test_store.py`:

```python
@pytest.mark.asyncio
async def test_list_all_reads_from_cache_not_redis(
    mock_redis: AsyncMock, snapshot_dir: Path
) -> None:
    """After load(), list_all() should return cached data without hitting Redis."""
    d1 = _make_trigger_dict("t-1")
    mock_redis.hgetall = AsyncMock(return_value={"t-1": json.dumps(d1)})
    store = TriggerStore(redis=mock_redis, snapshot_dir=snapshot_dir)
    await store.load()

    # Reset mock to track new calls
    mock_redis.hgetall.reset_mock()

    result = await store.list_all()
    assert len(result) == 1
    assert result[0].trigger_id == "t-1"
    # list_all should NOT have called hgetall again
    mock_redis.hgetall.assert_not_called()


@pytest.mark.asyncio
async def test_get_reads_from_cache(mock_redis: AsyncMock, snapshot_dir: Path) -> None:
    """After load(), get() should return from cache without hitting Redis."""
    d1 = _make_trigger_dict("t-1")
    mock_redis.hgetall = AsyncMock(return_value={"t-1": json.dumps(d1)})
    store = TriggerStore(redis=mock_redis, snapshot_dir=snapshot_dir)
    await store.load()

    mock_redis.hget = AsyncMock()
    result = await store.get("t-1")
    assert result is not None
    assert result.trigger_id == "t-1"
    mock_redis.hget.assert_not_called()


@pytest.mark.asyncio
async def test_get_returns_none_for_missing(mock_redis: AsyncMock, snapshot_dir: Path) -> None:
    """get() returns None for trigger not in cache."""
    mock_redis.hgetall = AsyncMock(return_value={})
    store = TriggerStore(redis=mock_redis, snapshot_dir=snapshot_dir)
    await store.load()
    result = await store.get("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_save_updates_cache(mock_redis: AsyncMock, snapshot_dir: Path) -> None:
    """save() should update the in-memory cache."""
    mock_redis.hgetall = AsyncMock(return_value={})
    store = TriggerStore(redis=mock_redis, snapshot_dir=snapshot_dir)
    await store.load()

    cls = TriggerRegistry.get("time")
    trigger = cls(**_make_trigger_dict("t-new"))
    await store.save(trigger)

    result = await store.get("t-new")
    assert result is not None
    assert result.trigger_id == "t-new"


@pytest.mark.asyncio
async def test_delete_removes_from_cache(mock_redis: AsyncMock, snapshot_dir: Path) -> None:
    """delete() should remove trigger from in-memory cache."""
    d1 = _make_trigger_dict("t-1")
    mock_redis.hgetall = AsyncMock(return_value={"t-1": json.dumps(d1)})
    store = TriggerStore(redis=mock_redis, snapshot_dir=snapshot_dir)
    await store.load()

    await store.delete("t-1")

    result = await store.get("t-1")
    assert result is None


@pytest.mark.asyncio
async def test_list_all_before_load_returns_empty(
    mock_redis: AsyncMock, snapshot_dir: Path
) -> None:
    """list_all() before load() returns empty, not AttributeError."""
    store = TriggerStore(redis=mock_redis, snapshot_dir=snapshot_dir)
    result = await store.list_all()
    assert result == []


@pytest.mark.asyncio
async def test_refresh_resyncs_from_redis(mock_redis: AsyncMock, snapshot_dir: Path) -> None:
    """refresh() should replace cache with current Redis state."""
    d1 = _make_trigger_dict("t-1")
    mock_redis.hgetall = AsyncMock(return_value={"t-1": json.dumps(d1)})
    store = TriggerStore(redis=mock_redis, snapshot_dir=snapshot_dir)
    await store.load()

    # Simulate external Redis change: t-1 removed, t-2 added
    d2 = _make_trigger_dict("t-2")
    mock_redis.hgetall = AsyncMock(return_value={"t-2": json.dumps(d2)})

    await store.refresh()

    all_triggers = await store.list_all()
    assert len(all_triggers) == 1
    assert all_triggers[0].trigger_id == "t-2"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest core/triggers/tests/test_store.py -v`
Expected: New tests fail (no `_cache` attribute, `list_all` still hits Redis, no `refresh` method).

- [ ] **Step 3: Update existing `test_list_all` to use cache pattern**

Replace the existing `test_list_all` test (lines 116-132 in `test_store.py`) — it currently calls `list_all()` without `load()` first. After the cache change, it must call `load()` to populate the cache:

```python
@pytest.mark.asyncio
async def test_list_all(mock_redis: AsyncMock, snapshot_dir: Path) -> None:
    d1 = _make_trigger_dict("t-1")
    d2 = _make_trigger_dict("t-2")
    d2["enabled"] = False
    mock_redis.hgetall = AsyncMock(
        return_value={
            "t-1": json.dumps(d1),
            "t-2": json.dumps(d2),
        }
    )
    store = TriggerStore(redis=mock_redis, snapshot_dir=snapshot_dir)
    await store.load()

    all_triggers = await store.list_all()
    assert len(all_triggers) == 2

    enabled_only = await store.list_all(enabled_only=True)
    assert len(enabled_only) == 1
```

- [ ] **Step 4: Implement the cache in store.py**

Modify `core/triggers/store.py`:

1. In `__init__`, add `self._cache: dict[str, BaseTrigger] = {}` after `self._snapshot_dir`.

2. Replace `load()` entirely with:
```python
async def load(self) -> list[BaseTrigger]:
    """Load all triggers from Redis, falling back to disk if empty."""
    raw: dict[str | bytes, str | bytes] = await self._redis.hgetall(TRIGGERS_KEY)  # type: ignore[misc]

    if raw:
        triggers = self._parse_redis_entries(raw)
        self._cache = {t.trigger_id: t for t in triggers}
        return triggers

    logger.info("Redis empty — rehydrating triggers from disk")
    triggers = self.rehydrate_from_disk_static(self._snapshot_dir, TriggerRegistry)
    for t in triggers:
        await self._redis.hset(TRIGGERS_KEY, t.trigger_id, t.model_dump_json())  # type: ignore[misc]
    self._cache = {t.trigger_id: t for t in triggers}
    return triggers
```

3. Replace `list_all()` body entirely:
```python
async def list_all(self, enabled_only: bool = False) -> list[BaseTrigger]:
    """Return all triggers from in-memory cache, optionally filtered."""
    triggers = list(self._cache.values())
    if enabled_only:
        return [t for t in triggers if t.enabled]
    return triggers
```

4. Replace `get()` body entirely:
```python
async def get(self, trigger_id: str) -> BaseTrigger | None:
    """Fetch a single trigger by ID from in-memory cache."""
    return self._cache.get(trigger_id)
```

5. In `save()`, after the YAML snapshot line (line 49), add:
```python
self._cache[trigger.trigger_id] = trigger
```

6. In `delete()`, after the YAML delete line (line 55), add:
```python
self._cache.pop(trigger_id, None)
```

7. Add new `refresh()` method after `delete()`:
```python
async def refresh(self) -> None:
    """Re-sync cache from Redis (safety net, called periodically)."""
    raw: dict[str | bytes, str | bytes] = await self._redis.hgetall(TRIGGERS_KEY)  # type: ignore[misc]
    self._cache = {
        t.trigger_id: t for t in self._parse_redis_entries(raw)
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest core/triggers/tests/test_store.py -v`
Expected: All tests pass including new cache tests.

- [ ] **Step 6: Run full trigger test suite**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest core/triggers/tests/ -v`
Expected: All tests pass. Engine tests use mock store so they are unaffected.

- [ ] **Step 7: Run linting and type checks**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && ruff check core/triggers/store.py && ruff format core/triggers/store.py && mypy --strict core/triggers/store.py`
Expected: Clean.

- [ ] **Step 8: Commit**

```bash
git add core/triggers/store.py core/triggers/tests/test_store.py
git commit -m "feat(triggers): add write-through in-memory cache to TriggerStore

Eliminates per-tick HGETALL. list_all() and get() now read from
_cache dict. save()/delete() write-through. New refresh() re-syncs
from Redis (intended for periodic safety-net, not hot path)."
```

---

## Task 3: CompositeTrigger Child Caching

**Files:**
- Modify: `core/triggers/types/composite.py`
- Modify: `core/triggers/tests/test_types_composite.py`

- [ ] **Step 1: Write failing tests for cached children**

Add these tests to `core/triggers/tests/test_types_composite.py`:

```python
def test_cached_children_populated() -> None:
    """model_post_init should pre-build child instances."""
    children = [
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.a", "state_match": "on"}},
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.b"}},
    ]
    trigger = _make_composite(conditions={"children": children, "require": 1})
    # Access private attr
    cached = trigger._cached_children  # type: ignore[attr-defined]
    assert len(cached) == 2
    assert cached[0].trigger_id == "t-1:child:0"
    assert cached[1].trigger_id == "t-1:child:1"


def test_cached_children_indexed_ids() -> None:
    """Each child should have a unique indexed trigger_id."""
    children = [
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.a"}},
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.b"}},
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.c"}},
    ]
    trigger = _make_composite(conditions={"children": children, "require": 1})
    cached = trigger._cached_children  # type: ignore[attr-defined]
    ids = [c.trigger_id for c in cached]
    assert ids == ["t-1:child:0", "t-1:child:1", "t-1:child:2"]


def test_model_copy_rebuilds_cached_children() -> None:
    """model_copy() should re-run model_post_init and rebuild children."""
    children = [
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.a"}},
    ]
    trigger = _make_composite(conditions={"children": children, "require": 1})
    copied = trigger.model_copy(update={"name": "renamed"})
    cached = copied._cached_children  # type: ignore[attr-defined]
    assert len(cached) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest core/triggers/tests/test_types_composite.py -v`
Expected: Fail — `_cached_children` attribute does not exist.

- [ ] **Step 3: Implement model_post_init in CompositeTrigger**

Replace `core/triggers/types/composite.py` entirely:

```python
"""CompositeTrigger — fires when N of M child conditions are met."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, PrivateAttr

from core.triggers.models import BaseTrigger, TriggerContext
from core.triggers.registry import TriggerRegistry


@TriggerRegistry.register_type("composite")
class CompositeTrigger(BaseTrigger):
    """Fires when at least `require` of the child conditions evaluate to True."""

    trigger_type: str = "composite"

    class Conditions(BaseModel):
        """Composite trigger conditions."""

        children: list[dict[str, Any]]
        require: int

    conditions: Conditions
    _cached_children: list[BaseTrigger] = PrivateAttr(default_factory=list)

    def model_post_init(self, __context: Any) -> None:
        """Pre-build child trigger instances at construction time."""
        children: list[BaseTrigger] = []
        for i, child_spec in enumerate(self.conditions.children):
            child_type = child_spec.get("trigger_type", "")
            child_conditions = child_spec.get("conditions", {})
            child_cls = TriggerRegistry.get(child_type)
            child = child_cls(
                trigger_id=f"{self.trigger_id}:child:{i}",
                trigger_type=child_type,
                name=f"{self.name}:child:{i}",
                created_by=self.created_by,
                created_at=self.created_at,
                conditions=child_conditions,
            )
            children.append(child)
        self._cached_children = children

    def evaluate(self, context: TriggerContext) -> bool:
        """Evaluate each cached child trigger and check if enough are satisfied."""
        matched = 0
        for child in self._cached_children:
            if child.evaluate(context):
                matched += 1
                if matched >= self.conditions.require:
                    return True
        return matched >= self.conditions.require
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest core/triggers/tests/test_types_composite.py -v`
Expected: All tests pass including new cache tests. Existing behavior tests (all_children_match, not_enough, mixed, partial_require) also pass.

- [ ] **Step 5: Run linting and type checks**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && ruff check core/triggers/types/composite.py && ruff format core/triggers/types/composite.py && mypy --strict core/triggers/types/composite.py`
Expected: Clean.

- [ ] **Step 6: Commit**

```bash
git add core/triggers/types/composite.py core/triggers/tests/test_types_composite.py
git commit -m "feat(triggers): cache child instances in CompositeTrigger via model_post_init

Pre-builds child BaseTrigger instances at construction time using
PrivateAttr. Indexed child IDs (child:0, child:1, ...) replace
the previous shared ID. evaluate() iterates cached list with zero
allocation."
```

---

## Task 4: TimeTrigger Cron Validation at Construction

**Files:**
- Modify: `core/triggers/types/time.py`
- Modify: `core/triggers/tests/test_types_time.py`

- [ ] **Step 1: Write failing tests for cron validation**

Add these tests to `core/triggers/tests/test_types_time.py`:

```python
def test_cached_cron_populated() -> None:
    """model_post_init should validate cron and store sentinel croniter."""
    trigger = _make_time_trigger(conditions={"cron": "0 7 * * *"})
    cached = trigger._cached_cron  # type: ignore[attr-defined]
    assert cached is not None


def test_cached_cron_none_for_run_at() -> None:
    """run_at triggers should have _cached_cron = None."""
    target = datetime(2026, 3, 10, 15, 0, 0, tzinfo=UTC)
    trigger = _make_time_trigger(conditions={"run_at": target.isoformat()})
    cached = trigger._cached_cron  # type: ignore[attr-defined]
    assert cached is None


def test_invalid_cron_fails_at_construction() -> None:
    """Bad cron expression should raise at construction, not at evaluate()."""
    with pytest.raises(ValueError, match="[Ii]nvalid|[Bb]ad"):
        _make_time_trigger(conditions={"cron": "not a cron"})


def test_model_copy_rebuilds_cached_cron() -> None:
    """model_copy() should re-run model_post_init."""
    trigger = _make_time_trigger(conditions={"cron": "0 7 * * *"})
    copied = trigger.model_copy(update={"name": "renamed"})  # type: ignore[union-attr]
    cached = copied._cached_cron  # type: ignore[attr-defined]
    assert cached is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest core/triggers/tests/test_types_time.py -v`
Expected: Fail — `_cached_cron` attribute does not exist, bad cron doesn't fail at construction.

- [ ] **Step 3: Implement model_post_init in TimeTrigger**

Replace `core/triggers/types/time.py` entirely:

```python
"""TimeTrigger — fires on cron schedule or specific datetime."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from croniter import croniter  # type: ignore[import-untyped]
from pydantic import BaseModel, PrivateAttr

from core.triggers.models import BaseTrigger, TriggerContext
from core.triggers.registry import TriggerRegistry


@TriggerRegistry.register_type("time")
class TimeTrigger(BaseTrigger):
    """Fires on a cron schedule or at a specific datetime."""

    trigger_type: str = "time"

    class Conditions(BaseModel):
        """Time-based trigger conditions."""

        cron: str | None = None
        run_at: datetime | None = None

    conditions: Conditions
    _cached_cron: croniter | None = PrivateAttr(default=None)

    def model_post_init(self, __context: Any) -> None:
        """Validate cron expression at construction time (fail-fast)."""
        if self.conditions.cron is not None:
            try:
                self._cached_cron = croniter(self.conditions.cron)
            except (ValueError, KeyError) as e:
                raise ValueError(
                    f"Invalid cron expression {self.conditions.cron!r}: {e}"
                ) from e

    def evaluate(self, context: TriggerContext) -> bool:
        """Check if the current time matches the cron or run_at condition."""
        now = context.now

        if self.conditions.cron is not None:
            cron = croniter(self.conditions.cron, now - timedelta(seconds=1))
            next_fire: datetime = cron.get_next(datetime)
            diff = abs((next_fire - now).total_seconds())
            return bool(diff < 1.0)

        if self.conditions.run_at is not None:
            target = self.conditions.run_at
            if target.tzinfo is None:
                target = target.replace(tzinfo=UTC)
            if now >= target:
                return self.last_fired is None or self.last_fired < target
            return False

        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest core/triggers/tests/test_types_time.py -v`
Expected: All tests pass including new validation tests and existing cron/run_at tests.

- [ ] **Step 5: Run linting and type checks**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && ruff check core/triggers/types/time.py && ruff format core/triggers/types/time.py && mypy --strict core/triggers/types/time.py`
Expected: Clean (croniter already has `ignore_missing_imports` via `import-untyped` comment).

- [ ] **Step 6: Commit**

```bash
git add core/triggers/types/time.py core/triggers/tests/test_types_time.py
git commit -m "feat(triggers): validate cron at construction in TimeTrigger

model_post_init creates a sentinel croniter to fail fast on bad
cron expressions. evaluate() still constructs per-tick (cron parsing
is cheap relative to the HGETALL elimination). Follow-up can cache
if profiling shows need."
```

---

## Task 5: FastAPI Server Replacement

**Files:**
- Rewrite: `core/triggers/server.py`
- Rewrite: `core/triggers/tests/test_server.py`

- [ ] **Step 1: Write tests for the new FastAPI server**

Rewrite `core/triggers/tests/test_server.py` entirely:

```python
"""Tests for trigger engine FastAPI server (REST + JSON-RPC shim)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from core.triggers.registry import TriggerRegistry


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TriggerRegistry, "_registry", {})
    import core.triggers.types  # noqa: F401


@pytest.fixture
def mock_feature() -> AsyncMock:
    feature = AsyncMock()
    feature.create_trigger = AsyncMock(
        return_value={"trigger_id": "t-1", "name": "test"}
    )
    feature.list_triggers = AsyncMock(return_value=[])
    feature.update_trigger = AsyncMock(
        return_value={"trigger_id": "t-1", "name": "updated"}
    )
    feature.delete_trigger = AsyncMock(return_value={"status": "deleted", "trigger_id": "t-1"})
    feature.toggle_trigger = AsyncMock(
        return_value={"trigger_id": "t-1", "enabled": False}
    )
    return feature


@pytest.fixture
def mock_client() -> AsyncMock:
    client = AsyncMock()
    client.dispatch = AsyncMock(return_value={"trigger_id": "t-1", "status": "created"})
    return client


@pytest.fixture
def test_client(mock_feature: AsyncMock, mock_client: AsyncMock) -> TestClient:
    from core.triggers.server import create_app

    app = create_app(client=mock_client, feature=mock_feature)
    return TestClient(app)


def test_health_check(test_client: TestClient) -> None:
    response = test_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_create_trigger(test_client: TestClient, mock_feature: AsyncMock) -> None:
    payload: dict[str, Any] = {
        "name": "test",
        "trigger_type": "time",
        "conditions": {"cron": "0 7 * * *"},
    }
    response = test_client.post("/triggers", json=payload)
    assert response.status_code == 200
    mock_feature.create_trigger.assert_called_once()


def test_list_triggers(test_client: TestClient, mock_feature: AsyncMock) -> None:
    response = test_client.get("/triggers")
    assert response.status_code == 200
    mock_feature.list_triggers.assert_called_once()


def test_update_trigger(test_client: TestClient, mock_feature: AsyncMock) -> None:
    payload: dict[str, Any] = {"name": "updated"}
    response = test_client.patch("/triggers/t-1", json=payload)
    assert response.status_code == 200
    mock_feature.update_trigger.assert_called_once()


def test_delete_trigger(test_client: TestClient, mock_feature: AsyncMock) -> None:
    response = test_client.delete("/triggers/t-1")
    assert response.status_code == 200
    mock_feature.delete_trigger.assert_called_once()


def test_toggle_trigger(test_client: TestClient, mock_feature: AsyncMock) -> None:
    response = test_client.patch("/triggers/t-1/toggle", json={"enabled": False})
    assert response.status_code == 200
    mock_feature.toggle_trigger.assert_called_once()


def test_jsonrpc_shim(test_client: TestClient, mock_client: AsyncMock) -> None:
    """JSON-RPC backward-compat shim delegates to AlfredClient.dispatch()."""
    rpc_request: dict[str, Any] = {
        "method": "triggers.create_trigger",
        "params": {"name": "test", "trigger_type": "time", "conditions": {"cron": "0 7 * * *"}},
        "id": "req-1",
    }
    response = test_client.post("/jsonrpc", json=rpc_request)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "req-1"
    assert "result" in data
    mock_client.dispatch.assert_called_once()


def test_jsonrpc_shim_error(test_client: TestClient, mock_client: AsyncMock) -> None:
    """JSON-RPC shim returns error when dispatch raises."""
    mock_client.dispatch = AsyncMock(side_effect=KeyError("unknown"))
    rpc_request: dict[str, Any] = {"method": "bad", "params": {}, "id": "req-1"}
    response = test_client.post("/jsonrpc", json=rpc_request)
    assert response.status_code == 200
    data = response.json()
    assert "error" in data
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest core/triggers/tests/test_server.py -v`
Expected: Fail — `create_app` does not exist yet.

- [ ] **Step 3: Implement the FastAPI server**

Rewrite `core/triggers/server.py` entirely:

```python
"""FastAPI server for trigger engine — REST endpoints + JSON-RPC backward-compat shim."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI

if TYPE_CHECKING:
    from core.triggers.feature import TriggerFeature
    from sdk.alfred_sdk.client import AlfredClient

logger = logging.getLogger(__name__)


def create_app(client: AlfredClient, feature: TriggerFeature) -> FastAPI:
    """Build the FastAPI app with REST routes and JSON-RPC shim."""
    app = FastAPI(title="Trigger Engine", docs_url="/docs")

    @app.get("/health")
    async def health_check() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/triggers")
    async def create_trigger(body: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = await feature.create_trigger(**body)
        return result

    @app.get("/triggers")
    async def list_triggers(enabled_only: bool = True) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = await feature.list_triggers(enabled_only=enabled_only)
        return result

    @app.patch("/triggers/{trigger_id}")
    async def update_trigger(trigger_id: str, body: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = await feature.update_trigger(trigger_id=trigger_id, **body)
        return result

    @app.delete("/triggers/{trigger_id}")
    async def delete_trigger(trigger_id: str) -> dict[str, str]:
        result: dict[str, str] = await feature.delete_trigger(trigger_id=trigger_id)
        return result

    @app.patch("/triggers/{trigger_id}/toggle")
    async def toggle_trigger(trigger_id: str, body: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = await feature.toggle_trigger(
            trigger_id=trigger_id, enabled=body["enabled"]
        )
        return result

    # JSON-RPC backward-compat shim
    @app.post("/jsonrpc")
    async def jsonrpc_shim(body: dict[str, Any]) -> dict[str, Any]:
        method = body.get("method", "")
        params = body.get("params", {})
        req_id = body.get("id")
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

    return app
```

Note: `run_server()` is removed. Task 6's `__main__.py` constructs the `uvicorn.Server` directly for shutdown control — `server.serve()` blocks until exit, so returning the server instance from an async wrapper is not viable.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest core/triggers/tests/test_server.py -v`
Expected: All tests pass.

- [ ] **Step 5: Run linting and type checks**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && ruff check core/triggers/server.py && ruff format core/triggers/server.py && mypy --strict core/triggers/server.py`
Expected: Clean.

- [ ] **Step 6: Commit**

```bash
git add core/triggers/server.py core/triggers/tests/test_server.py
git commit -m "feat(triggers): replace raw asyncio HTTP server with FastAPI

REST endpoints for trigger CRUD + /health check. JSON-RPC shim at
POST /jsonrpc preserves backward compatibility. run_server() returns
uvicorn.Server instance for graceful shutdown control."
```

---

## Task 6: __main__.py Integration (refresh_loop + uvicorn + shutdown)

**Files:**
- Modify: `core/triggers/__main__.py`

- [ ] **Step 1: Update imports at top of file**

Add these imports (after the existing `import signal` line):
```python
import uvicorn
```

Add after the existing `from core.triggers.store import TriggerStore` line:
```python
from core.triggers.server import create_app
```

Remove the lazy import inside `run()` at line 127: `from core.triggers.server import run_server` — this is no longer needed.

- [ ] **Step 2: Add refresh_loop function**

Add after `snapshot_loop` (after line 98):

```python
async def refresh_loop(store: TriggerStore, interval: float = 60.0) -> None:
    """Periodic cache refresh from Redis (safety net)."""
    while not _shutdown.is_set():
        await asyncio.sleep(interval)
        try:
            await store.refresh()
            logger.debug("Cache refresh complete")
        except Exception as e:
            logger.error("Cache refresh error: %s", e)
```

- [ ] **Step 3: Update run() to pass feature to server and handle uvicorn shutdown**

Replace the entire `run()` function with the version below. Key changes from original:
- Constructs `TriggerFeature` instance for both server and client registration
- Builds FastAPI app + `uvicorn.Server` directly (no `run_server()`)
- Adds `refresh_loop` task
- Graceful shutdown: `server.should_exit = True` before task cancellation
- `asyncio.gather(*tasks, return_exceptions=True)` to await cancelled tasks

Full updated `run()` function:

```python
async def run(config: AlfredConfig) -> None:
    """Main Trigger Engine loop."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    r: aioredis.Redis = aioredis.from_url(config.redis_url)

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
    ctx = TriggerFeatureContext(store=store, redis=r)
    feature = TriggerFeature(ctx)
    client.discover_features_from_classes([TriggerFeature], ctx=ctx)
    await client.register()
    logger.info("Registered trigger CRUD tools in tool registry")

    # Build FastAPI app + uvicorn server
    app = create_app(client=client, feature=feature)
    uvi_config = uvicorn.Config(app, host="0.0.0.0", port=8001, log_level="info")
    uvi_server = uvicorn.Server(uvi_config)

    tasks = [
        asyncio.create_task(tick_loop(engine)),
        asyncio.create_task(event_loop(engine, r)),
        asyncio.create_task(snapshot_loop(store)),
        asyncio.create_task(refresh_loop(store)),
        asyncio.create_task(uvi_server.serve()),
    ]

    logger.info("Trigger Engine started")

    try:
        await _shutdown.wait()
    finally:
        logger.info("Shutting down Trigger Engine...")
        uvi_server.should_exit = True
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.unregister()
        await r.aclose()
```

- [ ] **Step 4: Run full test suite**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest core/triggers/tests/ -v`
Expected: All tests pass.

- [ ] **Step 5: Run linting and type checks**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && ruff check core/triggers/__main__.py && ruff format core/triggers/__main__.py && mypy --strict core/triggers/__main__.py`
Expected: Clean.

- [ ] **Step 6: Commit**

```bash
git add core/triggers/__main__.py
git commit -m "feat(triggers): integrate refresh_loop, uvicorn server, graceful shutdown

refresh_loop re-syncs cache from Redis every 60s as safety net.
uvicorn.Server replaces asyncio.start_server. Graceful shutdown
sets server.should_exit before cancelling tasks."
```

---

## Task 7: Full Verification Pass

**Files:** All modified files

- [ ] **Step 1: Run full project test suite**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest core/triggers/tests/ -v --tb=short`
Expected: All tests pass.

- [ ] **Step 2: Run ruff on all changed files**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && ruff check core/triggers/ && ruff format --check core/triggers/`
Expected: Clean.

- [ ] **Step 3: Run mypy on all trigger modules**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && mypy --strict core/triggers/`
Expected: Clean (modulo existing `# type: ignore` comments for redis awaitable pattern).

- [ ] **Step 4: Run broader test suite to catch regressions**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest -v --tb=short`
Expected: All tests pass. No regressions in bus, domains, evals, sdk, etc.

- [ ] **Step 5: Update backlog**

Mark items 1, 2, 3 as complete in `docs/backlog/trigger-engine-simplification.md`. Add a note that TimeTrigger cron validation was also included (not in original backlog).

- [ ] **Step 6: Commit backlog update**

```bash
git add docs/backlog/trigger-engine-simplification.md
git commit -m "docs: mark trigger engine hardening items 1-3 complete in backlog"
```
