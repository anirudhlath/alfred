# Instant Triggers + Client Timezone Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reminders become visible to the triggers process in milliseconds (Redis pub/sub cache coherence) and fire at their exact due time (scheduled wakeup replaces the 1s tick); Alfred learns the client's IANA timezone from web/iOS and uses it for the prompt clock, `run_at`, cron, and routine patterns.

**Architecture:** `TriggerStore` owns cache coherence — `save()`/`delete()` publish on a pub/sub channel, every store instance runs a subscriber that applies single-trigger updates; the 60s full refresh stays as reconciliation. The 1s tick loop is replaced by a scheduler that sleeps until the earliest `next_fire_time()` and is woken by any mutation. Timezone flows client → `UserRequest.timezone` → Redis key `alfred:user:timezone` → prompt rendering + trigger evaluation.

**Tech Stack:** Python 3.13 (asyncio, zoneinfo, croniter, Pydantic v2, redis.asyncio), TypeScript (Vite/vitest), Swift (AlfredKit).

**Spec:** `docs/superpowers/specs/2026-07-15-instant-triggers-client-timezone-design.md`
(One deliberate deviation: `get_user_timezone()` returns a validated IANA **string**, not `ZoneInfo` — Pydantic-friendly for `TriggerContext.tz`; callers wrap in `ZoneInfo(name)`, which is cached and cheap.)

## Global Constraints

- Python 3.13+, `uv`; run tests as `.venv/bin/python -m pytest` from the worktree root.
- `ruff check . --fix && ruff format .` (line length 100) and `mypy --strict bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/` must pass — **note `core/triggers/tests/` is under `core/`, so test code must be strict-typed too**.
- Redis awaitable calls need `# type: ignore[misc]` (see `core/triggers/store.py` precedent).
- Stream/key constants come from `shared.streams`; `AioRedis` from `shared.types` — never redefine.
- Use `logging.getLogger(__name__)` (loguru intercepts stdlib) — consistent with the modified modules.
- No polling: pub/sub subscriber uses `pubsub.listen()` (blocking read), scheduler sleeps on an `asyncio.Event` with an exact timeout.
- Reconciliation refresh interval stays **60s** (user decision).
- Single-user timezone key (YAGNI — no per-identity keys).
- Commit after every task; messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Timezone constants + `shared/usertime.py`

**Files:**
- Modify: `shared/streams.py` (add two constants)
- Create: `shared/usertime.py`
- Test: `tests/shared/test_usertime.py`

**Interfaces:**
- Produces: `TRIGGERS_CHANGED_CHANNEL = "alfred:triggers:changed"`, `USER_TIMEZONE_KEY = "alfred:user:timezone"` (in `shared.streams`); `is_valid_timezone(name: str) -> bool`, `async get_user_timezone(redis: AioRedis) -> str`, `async set_user_timezone(redis: AioRedis, tz_name: str) -> bool` (in `shared.usertime`).

- [ ] **Step 1: Write the failing tests** — `tests/shared/test_usertime.py`:

```python
"""Tests for shared.usertime — user timezone resolution helpers."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from shared.streams import TRIGGERS_CHANGED_CHANNEL, USER_TIMEZONE_KEY
from shared.usertime import get_user_timezone, is_valid_timezone, set_user_timezone


def test_is_valid_timezone() -> None:
    assert is_valid_timezone("America/Denver")
    assert is_valid_timezone("UTC")
    assert not is_valid_timezone("Not/AZone")
    assert not is_valid_timezone("")


@pytest.mark.asyncio
async def test_get_returns_stored_value() -> None:
    r = AsyncMock()
    r.get = AsyncMock(return_value=b"America/Denver")
    assert await get_user_timezone(r) == "America/Denver"
    r.get.assert_awaited_once_with(USER_TIMEZONE_KEY)


@pytest.mark.asyncio
async def test_get_falls_back_to_env_then_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    r = AsyncMock()
    r.get = AsyncMock(return_value=None)
    monkeypatch.setenv("ALFRED_TIMEZONE", "Europe/London")
    assert await get_user_timezone(r) == "Europe/London"
    monkeypatch.delenv("ALFRED_TIMEZONE")
    assert await get_user_timezone(r) == "UTC"


@pytest.mark.asyncio
async def test_get_ignores_invalid_stored_value() -> None:
    r = AsyncMock()
    r.get = AsyncMock(return_value="garbage")
    assert await get_user_timezone(r) == "UTC"


@pytest.mark.asyncio
async def test_set_writes_and_pokes_channel_on_change() -> None:
    r = AsyncMock()
    r.get = AsyncMock(return_value=None)
    assert await set_user_timezone(r, "America/Denver") is True
    r.set.assert_awaited_once_with(USER_TIMEZONE_KEY, "America/Denver")
    r.publish.assert_awaited_once_with(
        TRIGGERS_CHANGED_CHANNEL, json.dumps({"op": "tz-changed"})
    )


@pytest.mark.asyncio
async def test_set_skips_write_when_unchanged() -> None:
    r = AsyncMock()
    r.get = AsyncMock(return_value=b"America/Denver")
    assert await set_user_timezone(r, "America/Denver") is False
    r.set.assert_not_awaited()
    r.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_rejects_invalid_timezone() -> None:
    r = AsyncMock()
    assert await set_user_timezone(r, "Not/AZone") is False
    r.set.assert_not_awaited()
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest tests/shared/test_usertime.py -q` → FAIL (`ModuleNotFoundError: shared.usertime` / ImportError on constants).

- [ ] **Step 3: Implement.** Append to `shared/streams.py` (after `INTEGRATION_REGISTRY_KEY` block):

```python
# Trigger cache coherence + user timezone
TRIGGERS_CHANGED_CHANNEL = "alfred:triggers:changed"
USER_TIMEZONE_KEY = "alfred:user:timezone"
```

Create `shared/usertime.py`:

```python
"""User timezone helpers — single source of truth for the user's local timezone.

Resolution order: stored Redis key -> ALFRED_TIMEZONE env -> UTC.
Single-user by design (one key); per-identity keys are a future extension.
"""

from __future__ import annotations

import json
import logging
import os
from zoneinfo import ZoneInfo

from shared.streams import TRIGGERS_CHANGED_CHANNEL, USER_TIMEZONE_KEY
from shared.types import AioRedis

logger = logging.getLogger(__name__)


def is_valid_timezone(tz_name: str) -> bool:
    """Return True if *tz_name* is a resolvable IANA timezone name."""
    if not tz_name:
        return False
    try:
        ZoneInfo(tz_name)
    except Exception:
        return False
    return True


async def get_user_timezone(redis: AioRedis) -> str:
    """Return the user's IANA timezone name (stored -> env -> UTC)."""
    raw: str | bytes | None = await redis.get(USER_TIMEZONE_KEY)  # type: ignore[misc]
    if raw is not None:
        name = raw.decode() if isinstance(raw, bytes) else raw
        if is_valid_timezone(name):
            return name
    env = os.getenv("ALFRED_TIMEZONE", "")
    if env and is_valid_timezone(env):
        return env
    return "UTC"


async def set_user_timezone(redis: AioRedis, tz_name: str) -> bool:
    """Persist the user's timezone if valid and changed.

    On change, pokes TRIGGERS_CHANGED_CHANNEL so long-sleeping cron alarms
    re-arm under the new zone. Returns True only when a write happened.
    """
    if not is_valid_timezone(tz_name):
        logger.warning("Ignoring invalid client timezone %r", tz_name)
        return False
    raw: str | bytes | None = await redis.get(USER_TIMEZONE_KEY)  # type: ignore[misc]
    current = raw.decode() if isinstance(raw, bytes) else raw
    if current == tz_name:
        return False
    await redis.set(USER_TIMEZONE_KEY, tz_name)  # type: ignore[misc]
    await redis.publish(TRIGGERS_CHANGED_CHANNEL, json.dumps({"op": "tz-changed"}))  # type: ignore[misc]
    logger.info("User timezone set to %s", tz_name)
    return True
```

- [ ] **Step 4: Run to verify pass** — `.venv/bin/python -m pytest tests/shared/test_usertime.py -q` → all pass. Also `mypy --strict shared/` → clean.

- [ ] **Step 5: Commit** — `git add shared/streams.py shared/usertime.py tests/shared/test_usertime.py && git commit -m "feat(shared): user timezone helpers + trigger coherence channel constant"`

---

### Task 2: Clock semantics — `TriggerContext.tz`, `next_fire_time`, computed cron, boundary normalization

**Files:**
- Modify: `core/triggers/models.py` (TriggerContext + two BaseTrigger methods)
- Modify: `core/triggers/types/time.py` (rewrite evaluate, add next_fire_time/_aware_run_at/normalize_conditions, Field descriptions)
- Test: `core/triggers/tests/test_types_time.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces: `TriggerContext.tz: str = "UTC"` (IANA name); `BaseTrigger.next_fire_time(context: TriggerContext) -> datetime | None` (default None); `BaseTrigger.normalize_conditions(conditions: dict[str, Any], tz_name: str) -> dict[str, Any]` (classmethod, default pass-through). Task 3–6 rely on these exact names.

- [ ] **Step 1: Write the failing tests** — append to `core/triggers/tests/test_types_time.py`:

```python
from datetime import timedelta


def _ctx(now: datetime, tz: str = "UTC") -> TriggerContext:
    return TriggerContext(now=now, tz=tz)


# --- next_fire_time: run_at ---

def test_next_fire_time_run_at_pending() -> None:
    due = datetime(2026, 7, 16, 15, 0, tzinfo=UTC)
    t = _make_time_trigger(conditions={"run_at": due.isoformat()})
    assert t.next_fire_time(_ctx(due - timedelta(seconds=5))) == due


def test_next_fire_time_run_at_already_fired_returns_none() -> None:
    due = datetime(2026, 7, 16, 15, 0, tzinfo=UTC)
    t = _make_time_trigger(conditions={"run_at": due.isoformat()}, last_fired=due)
    assert t.next_fire_time(_ctx(due + timedelta(seconds=1))) is None


def test_legacy_naive_run_at_interpreted_as_utc() -> None:
    t = _make_time_trigger(conditions={"run_at": "2026-07-16T15:00:00"})
    assert t.evaluate(_ctx(datetime(2026, 7, 16, 15, 0, 1, tzinfo=UTC))) is True
    assert t.evaluate(_ctx(datetime(2026, 7, 16, 14, 59, 59, tzinfo=UTC))) is False


# --- next_fire_time + evaluate: cron (computed, not window-matched) ---

def test_cron_next_fire_time_in_user_timezone() -> None:
    t = _make_time_trigger(
        conditions={"cron": "0 7 * * *"},
        created_at=datetime(2026, 7, 15, 20, 0, tzinfo=UTC),  # 14:00 Denver
    )
    nft = t.next_fire_time(_ctx(datetime(2026, 7, 15, 20, 0, tzinfo=UTC), tz="America/Denver"))
    assert nft is not None
    assert nft.hour == 7 and str(nft.tzinfo) == "America/Denver"
    assert nft.astimezone(UTC) == datetime(2026, 7, 16, 13, 0, tzinfo=UTC)  # 7am MDT


def test_cron_dst_transition() -> None:
    # US DST starts 2026-03-08: 7am Denver goes from UTC-7 to UTC-6.
    t = _make_time_trigger(
        conditions={"cron": "0 7 * * *"},
        last_fired=datetime(2026, 3, 7, 14, 0, tzinfo=UTC),  # fired 7am MST Mar 7
        created_at=datetime(2026, 3, 1, 0, 0, tzinfo=UTC),
    )
    nft = t.next_fire_time(_ctx(datetime(2026, 3, 7, 15, 0, tzinfo=UTC), tz="America/Denver"))
    assert nft is not None
    assert nft.hour == 7
    assert nft.utcoffset() == timedelta(hours=-6)  # MDT after the spring-forward


def test_cron_late_wakeup_fires_exactly_once() -> None:
    t = _make_time_trigger(
        conditions={"cron": "0 7 * * *"},
        last_fired=datetime(2026, 7, 13, 7, 0, tzinfo=UTC),
        created_at=datetime(2026, 7, 1, 0, 0, tzinfo=UTC),
    )
    late_now = datetime(2026, 7, 16, 9, 0, tzinfo=UTC)  # slept through 3 boundaries
    assert t.evaluate(_ctx(late_now)) is True  # catch-up fire
    fired = t.model_copy(update={"last_fired": late_now})
    assert fired.evaluate(_ctx(late_now)) is False  # re-anchored, no repeat


def test_cron_does_not_fire_before_first_boundary() -> None:
    t = _make_time_trigger(
        conditions={"cron": "0 7 * * *"},
        created_at=datetime(2026, 7, 16, 8, 0, tzinfo=UTC),  # created after today's 7am
    )
    assert t.evaluate(_ctx(datetime(2026, 7, 16, 9, 0, tzinfo=UTC))) is False


# --- normalize_conditions ---

def test_normalize_naive_run_at_uses_user_timezone() -> None:
    cls = TriggerRegistry.get("time")
    out = cls.normalize_conditions({"run_at": "2026-07-16T15:00:00"}, "America/Denver")
    parsed = datetime.fromisoformat(out["run_at"])
    assert parsed.utcoffset() == timedelta(hours=-6)


def test_normalize_preserves_explicit_offset() -> None:
    cls = TriggerRegistry.get("time")
    out = cls.normalize_conditions({"run_at": "2026-07-16T15:00:00+02:00"}, "America/Denver")
    assert datetime.fromisoformat(out["run_at"]).utcoffset() == timedelta(hours=2)


def test_normalize_without_run_at_is_noop() -> None:
    cls = TriggerRegistry.get("time")
    conditions = {"cron": "0 7 * * *"}
    assert cls.normalize_conditions(conditions, "America/Denver") == conditions
```

(Also update the existing `test_cron_match` if it asserts the old `diff < 1.0` window behavior at an off-boundary instant: under computed semantics a trigger whose boundary just passed evaluates True until `last_fired` is set. Adjust its assertions to the new semantics rather than deleting it.)

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest core/triggers/tests/test_types_time.py -q` → FAIL (`tz` unexpected kwarg / missing methods).

- [ ] **Step 3: Implement.** In `core/triggers/models.py`:

```python
class TriggerContext(BaseModel):
    """Read-only context passed to evaluate()."""

    now: datetime
    tz: str = "UTC"  # IANA timezone name for wall-clock semantics (cron, patterns)
    event: StateChangedEvent | None = None
```

And on `BaseTrigger` (after the `evaluate` abstractmethod):

```python
    def next_fire_time(self, context: TriggerContext) -> datetime | None:
        """Next moment this trigger could fire based on the clock alone.

        None means "not clock-driven" — the trigger only responds to events.
        May return a past datetime; the scheduler evaluates immediately then
        excludes non-firing past candidates from the next alarm.
        """
        return None

    @classmethod
    def normalize_conditions(cls, conditions: dict[str, Any], tz_name: str) -> dict[str, Any]:
        """Normalize raw tool-call conditions before validation.

        Default: unchanged. Types with timezone-sensitive fields override
        (e.g. TimeTrigger localizes naive run_at to the user's timezone).
        """
        return conditions
```

In `core/triggers/types/time.py` — replace `evaluate` and add the new methods (imports: add `from zoneinfo import ZoneInfo` and `from pydantic import BaseModel, Field, PrivateAttr`; `timedelta` import becomes unused — remove it):

```python
    class Conditions(BaseModel):
        """Time-based trigger conditions."""

        cron: str | None = Field(
            default=None,
            description="5-field cron schedule, evaluated in the user's local timezone",
        )
        run_at: datetime | None = Field(
            default=None,
            description=(
                "Absolute due time, ISO-8601 WITH UTC offset "
                "(e.g. 2026-07-16T15:00:00-06:00). Naive values are interpreted "
                "in the user's local timezone."
            ),
        )
```

```python
    def _aware_run_at(self) -> datetime | None:
        """run_at as an aware datetime. Legacy naive values (pre-timezone data,
        computed against a UTC prompt) are interpreted as UTC; new writes are
        normalized at the tool boundary and always carry an offset."""
        target = self.conditions.run_at
        if target is None:
            return None
        if target.tzinfo is None:
            return target.replace(tzinfo=UTC)
        return target

    def next_fire_time(self, context: TriggerContext) -> datetime | None:
        run_at = self._aware_run_at()
        if run_at is not None:
            if self.last_fired is not None and self.last_fired >= run_at:
                return None
            return run_at
        if self.conditions.cron is not None:
            anchor = self.last_fired or self.created_at
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=UTC)
            local_anchor = anchor.astimezone(ZoneInfo(context.tz))
            nxt: datetime = croniter(self.conditions.cron, local_anchor).get_next(datetime)
            return nxt
        return None

    def evaluate(self, context: TriggerContext) -> bool:
        """Fire when the computed next fire time has been reached.

        Cron: next boundary strictly after (last_fired or created_at) — a late
        wakeup fires exactly once, then re-anchors. Replaces the old <1s
        tick-window match, which silently skipped fires on a busy loop.
        """
        target = self.next_fire_time(context)
        return target is not None and context.now >= target

    @classmethod
    def normalize_conditions(cls, conditions: dict[str, Any], tz_name: str) -> dict[str, Any]:
        run_at = conditions.get("run_at")
        if run_at is None:
            return conditions
        dt = (
            run_at
            if isinstance(run_at, datetime)
            else datetime.fromisoformat(str(run_at).replace("Z", "+00:00"))
        )
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo(tz_name))
        return {**conditions, "run_at": dt.isoformat()}
```

- [ ] **Step 4: Run to verify pass** — `.venv/bin/python -m pytest core/triggers/tests/ -q` (whole trigger suite — catches regressions in engine/feature tests that relied on old cron semantics) → pass. `mypy --strict core/triggers/` → clean.

- [ ] **Step 5: Commit** — `git commit -am "feat(triggers): computed next_fire_time, tz-aware cron, boundary run_at normalization"`

---

### Task 3: CompositeTrigger — `next_fire_time` + `last_fired` propagation to children

**Files:**
- Modify: `core/triggers/types/composite.py`
- Test: `core/triggers/tests/test_types_composite.py` (append)

**Interfaces:**
- Consumes: `BaseTrigger.next_fire_time(context)` from Task 2.
- Produces: composite scheduling participates in Task 5's `next_wakeup`.

- [ ] **Step 1: Write the failing tests** — append to `core/triggers/tests/test_types_composite.py` (reuse that file's existing helper/fixture conventions for constructing composites):

```python
def test_next_fire_time_is_min_over_time_children() -> None:
    created = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
    trigger = _make_composite(  # match the file's existing construction helper
        children=[
            {"trigger_type": "time", "conditions": {"run_at": "2026-07-16T15:00:00+00:00"}},
            {"trigger_type": "time", "conditions": {"run_at": "2026-07-16T12:00:00+00:00"}},
            {"trigger_type": "sensor", "conditions": {"entity_id": "light.x"}},
        ],
        require=1,
        created_at=created,
    )
    ctx = TriggerContext(now=created)
    assert trigger.next_fire_time(ctx) == datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def test_next_fire_time_none_when_only_sensor_children() -> None:
    trigger = _make_composite(
        children=[{"trigger_type": "sensor", "conditions": {"entity_id": "light.x"}}],
        require=1,
    )
    assert trigger.next_fire_time(TriggerContext(now=datetime.now(UTC))) is None


def test_children_inherit_parent_last_fired() -> None:
    fired = datetime(2026, 7, 16, 7, 0, tzinfo=UTC)
    trigger = _make_composite(
        children=[{"trigger_type": "time", "conditions": {"cron": "0 7 * * *"}}],
        require=1,
        created_at=datetime(2026, 7, 1, 0, 0, tzinfo=UTC),
    )
    fired_copy = trigger.model_copy(update={"last_fired": fired})
    # Child cron must anchor from the parent's last_fired, not created_at —
    # otherwise a composite cron child re-fires on every scheduler wake.
    child_nft = fired_copy._cached_children[0].next_fire_time(TriggerContext(now=fired))
    assert child_nft is not None and child_nft.astimezone(UTC) > fired
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest core/triggers/tests/test_types_composite.py -q` → FAIL.

- [ ] **Step 3: Implement.** In `CompositeTrigger.model_post_init`, pass the parent's dedupe anchor to children:

```python
            child = child_cls(
                trigger_id=f"{self.trigger_id}:child:{i}",
                trigger_type=child_type,
                name=f"{self.name}:child:{i}",
                created_by=self.created_by,
                created_at=self.created_at,
                last_fired=self.last_fired,
                conditions=child_conditions,
            )
```

Add after `evaluate`:

```python
    def next_fire_time(self, context: TriggerContext) -> datetime | None:
        """Earliest clock candidate among children (None if none are clock-driven)."""
        candidates = [
            t for c in self._cached_children if (t := c.next_fire_time(context)) is not None
        ]
        return min(candidates, default=None)
```

- [ ] **Step 4: Run to verify pass** — `.venv/bin/python -m pytest core/triggers/tests/ -q` → pass; mypy clean.

- [ ] **Step 5: Commit** — `git commit -am "feat(triggers): composite next_fire_time + last_fired propagation to children"`

---

### Task 4: TriggerStore pub/sub cache coherence

**Files:**
- Modify: `core/triggers/store.py`
- Modify: `core/triggers/tests/conftest.py` (append FakeRedis)
- Test: `core/triggers/tests/test_store.py` (append)

**Interfaces:**
- Consumes: `TRIGGERS_CHANGED_CHANNEL` (Task 1).
- Produces: `store.add_on_change(cb: Callable[[], None])`, `await store.start_sync()`, `await store.stop_sync()`; `save()`/`delete()` publish `{"op": "saved"|"deleted", "trigger_id": ...}`; `refresh()` fires on_change. Tasks 5–6 rely on these names.

- [ ] **Step 1: Append FakeRedis to `core/triggers/tests/conftest.py`** (keep existing content; add imports it needs):

```python
import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

import pytest


class FakePubSub:
    """Minimal async pub/sub compatible with redis.asyncio's PubSub surface."""

    def __init__(self, hub: "FakeRedis") -> None:
        self._hub = hub
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def subscribe(self, channel: str) -> None:
        self._hub.subscribers.setdefault(channel, []).append(self._queue)

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            yield await self._queue.get()

    async def aclose(self) -> None:
        for queues in self._hub.subscribers.values():
            with contextlib.suppress(ValueError):
                queues.remove(self._queue)


class FakeRedis:
    """In-memory Redis stub: hash + kv + streams + pub/sub broadcast."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.kv: dict[str, str] = {}
        self.streams: dict[str, list[dict[str, str]]] = {}
        self.lists: dict[str, list[str]] = {}
        self.subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}

    def pubsub(self) -> FakePubSub:
        return FakePubSub(self)

    async def hset(self, key: str, field: str, value: str) -> None:
        self.hashes.setdefault(key, {})[field] = value

    async def hget(self, key: str, field: str) -> str | None:
        return self.hashes.get(key, {}).get(field)

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    async def hdel(self, key: str, field: str) -> None:
        self.hashes.get(key, {}).pop(field, None)

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)

    async def set(self, key: str, value: str) -> None:
        self.kv[key] = value

    async def xadd(self, stream: str, fields: dict[str, str]) -> None:
        self.streams.setdefault(stream, []).append(fields)

    async def lpush(self, key: str, value: str) -> None:
        self.lists.setdefault(key, []).insert(0, value)

    async def publish(self, channel: str, message: str) -> None:
        for q in self.subscribers.get(channel, []):
            q.put_nowait({"type": "message", "channel": channel, "data": message})


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()
```

- [ ] **Step 2: Write the failing tests** — append to `core/triggers/tests/test_store.py` (import `asyncio` and the trigger builder already in the file):

```python
def _build_trigger(trigger_id: str = "t-sync") -> Any:
    cls = TriggerRegistry.get("time")
    return cls(**_make_trigger_dict(trigger_id))


@pytest.mark.asyncio
async def test_cross_process_visibility_without_refresh(
    fake_redis: Any, snapshot_dir: Path, tmp_path: Path
) -> None:
    """The 5s-reminder regression: a save in store A must appear in store B
    via pub/sub alone — no refresh(), no 60s wait."""
    store_a = TriggerStore(redis=fake_redis, snapshot_dir=snapshot_dir)
    store_b = TriggerStore(redis=fake_redis, snapshot_dir=tmp_path / "b")
    await store_b.start_sync()
    await asyncio.sleep(0.05)  # let the subscriber task subscribe
    try:
        await store_a.save(_build_trigger())
        await asyncio.sleep(0.05)  # let the message propagate
        assert await store_b.get("t-sync") is not None
    finally:
        await store_b.stop_sync()


@pytest.mark.asyncio
async def test_delete_propagates_and_fires_on_change(
    fake_redis: Any, snapshot_dir: Path, tmp_path: Path
) -> None:
    store_a = TriggerStore(redis=fake_redis, snapshot_dir=snapshot_dir)
    store_b = TriggerStore(redis=fake_redis, snapshot_dir=tmp_path / "b")
    changes: list[bool] = []
    store_b.add_on_change(lambda: changes.append(True))
    await store_b.start_sync()
    await asyncio.sleep(0.05)
    try:
        await store_a.save(_build_trigger())
        await asyncio.sleep(0.05)
        assert await store_b.get("t-sync") is not None
        await store_a.delete("t-sync")
        await asyncio.sleep(0.05)
        assert await store_b.get("t-sync") is None
        assert changes  # subscriber fired callbacks
    finally:
        await store_b.stop_sync()


@pytest.mark.asyncio
async def test_tz_changed_message_fires_callbacks_without_cache_mutation(
    fake_redis: Any, snapshot_dir: Path
) -> None:
    store = TriggerStore(redis=fake_redis, snapshot_dir=snapshot_dir)
    changes: list[bool] = []
    store.add_on_change(lambda: changes.append(True))
    await store._apply_sync_message('{"op": "tz-changed"}')
    assert changes


@pytest.mark.asyncio
async def test_saved_message_for_missing_trigger_evicts(
    fake_redis: Any, snapshot_dir: Path
) -> None:
    store = TriggerStore(redis=fake_redis, snapshot_dir=snapshot_dir)
    store._cache["ghost"] = _build_trigger("ghost")
    await store._apply_sync_message('{"op": "saved", "trigger_id": "ghost"}')
    assert await store.get("ghost") is None  # raced with a delete -> evict


@pytest.mark.asyncio
async def test_save_notifies_local_callbacks_immediately(
    fake_redis: Any, snapshot_dir: Path
) -> None:
    store = TriggerStore(redis=fake_redis, snapshot_dir=snapshot_dir)
    changes: list[bool] = []
    store.add_on_change(lambda: changes.append(True))
    await store.save(_build_trigger())
    assert changes  # no subscriber running — local notify is synchronous
```

- [ ] **Step 3: Run to verify failure** — `.venv/bin/python -m pytest core/triggers/tests/test_store.py -q` → FAIL (no `start_sync` etc.).

- [ ] **Step 4: Implement in `core/triggers/store.py`.** Imports: add `import contextlib`, `from collections.abc import Callable`, and extend the streams import to `from shared.streams import TRIGGERS_CHANGED_CHANNEL, TRIGGERS_KEY` plus `from shared.streams import decode_stream_value` if not present. In `__init__` add:

```python
        self._on_change: list[Callable[[], None]] = []
        self._sync_task: asyncio.Task[None] | None = None
```

New public/protected methods:

```python
    def add_on_change(self, callback: Callable[[], None]) -> None:
        """Register a synchronous callback fired after any cache change."""
        self._on_change.append(callback)

    def _notify_change(self) -> None:
        for callback in self._on_change:
            try:
                callback()
            except Exception:
                logger.exception("Trigger on_change callback failed")

    async def _publish_change(self, op: str, trigger_id: str) -> None:
        """Best-effort pub/sub poke; reconciliation refresh covers misses."""
        try:
            await self._redis.publish(  # type: ignore[misc]
                TRIGGERS_CHANGED_CHANNEL,
                json.dumps({"op": op, "trigger_id": trigger_id}),
            )
        except Exception as e:
            logger.error("Trigger change publish failed: %s", e)

    async def start_sync(self) -> None:
        """Start the pub/sub subscriber keeping this cache coherent."""
        if self._sync_task is None:
            self._sync_task = asyncio.create_task(self._sync_loop())

    async def stop_sync(self) -> None:
        if self._sync_task is not None:
            self._sync_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sync_task
            self._sync_task = None

    async def _sync_loop(self) -> None:
        while True:
            pubsub = self._redis.pubsub()
            try:
                await pubsub.subscribe(TRIGGERS_CHANGED_CHANNEL)
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    await self._apply_sync_message(decode_stream_value(message["data"]))
            except asyncio.CancelledError:
                with contextlib.suppress(Exception):
                    await pubsub.aclose()
                raise
            except Exception as e:
                logger.error("Trigger sync subscriber error: %s — resubscribing", e)
                with contextlib.suppress(Exception):
                    await pubsub.aclose()
                await asyncio.sleep(1.0)
                # Heal anything missed while disconnected (also notifies).
                with contextlib.suppress(Exception):
                    await self.refresh()

    async def _apply_sync_message(self, raw: str) -> None:
        try:
            data: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Malformed trigger sync message: %r", raw)
            return
        op = data.get("op")
        trigger_id = str(data.get("trigger_id", ""))
        if op == "saved":
            value: str | bytes | None = await self._redis.hget(  # type: ignore[misc]
                TRIGGERS_KEY, trigger_id
            )
            if value is None:
                self._cache.pop(trigger_id, None)  # raced with a delete
            else:
                parsed = self._parse_redis_entries({trigger_id: value})
                if parsed:
                    self._cache[trigger_id] = parsed[0]
        elif op == "deleted":
            self._cache.pop(trigger_id, None)
        elif op != "tz-changed":
            logger.warning("Unknown trigger sync op: %r", op)
            return
        self._notify_change()
```

Wire into the existing methods: at the end of `save()` add `await self._publish_change("saved", trigger.trigger_id)` then `self._notify_change()`; at the end of `delete()` add `await self._publish_change("deleted", trigger_id)` then `self._notify_change()`; at the end of `refresh()` add `self._notify_change()`. Update `refresh()`'s docstring (it is now the reconciliation net behind pub/sub, still 60s, still not for the hot path).

- [ ] **Step 5: Run to verify pass** — `.venv/bin/python -m pytest core/triggers/tests/ tests/shared/ -q` → pass; `mypy --strict core/ shared/` → clean.

- [ ] **Step 6: Commit** — `git commit -am "feat(triggers): pub/sub cache coherence in TriggerStore"`

---

### Task 5: TriggerEngine — tz-aware contexts + `next_wakeup`

**Files:**
- Modify: `core/triggers/engine.py`
- Test: `core/triggers/tests/test_engine.py` (append)

**Interfaces:**
- Consumes: `get_user_timezone` (Task 1), `next_fire_time` (Tasks 2–3), FakeRedis (Task 4).
- Produces: `await engine.next_wakeup(now: datetime) -> datetime | None`; `evaluate_tick`/`evaluate_event` now build `TriggerContext` with the resolved `tz`. Task 6's scheduler relies on `next_wakeup`.

- [ ] **Step 1: Write the failing tests** — append to `core/triggers/tests/test_engine.py` (reuse its existing store/engine fixtures; construct time triggers via `TriggerRegistry.get("time")` as in Task 2's tests):

```python
@pytest.mark.asyncio
async def test_next_wakeup_returns_earliest_future_candidate(
    fake_redis: Any, snapshot_dir: Path
) -> None:
    store = TriggerStore(redis=fake_redis, snapshot_dir=snapshot_dir)
    engine = TriggerEngine(store=store, redis=fake_redis)
    now = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
    cls = TriggerRegistry.get("time")
    for i, offset_min in enumerate((30, 10)):
        await store.save(
            cls(
                trigger_id=f"t-{i}",
                trigger_type="time",
                name=f"t-{i}",
                created_by="test",
                created_at=now,
                conditions={"run_at": (now + timedelta(minutes=offset_min)).isoformat()},
            )
        )
    assert await engine.next_wakeup(now) == now + timedelta(minutes=10)


@pytest.mark.asyncio
async def test_next_wakeup_excludes_past_due_and_none(
    fake_redis: Any, snapshot_dir: Path
) -> None:
    store = TriggerStore(redis=fake_redis, snapshot_dir=snapshot_dir)
    engine = TriggerEngine(store=store, redis=fake_redis)
    now = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
    cls = TriggerRegistry.get("time")
    await store.save(
        cls(
            trigger_id="past",
            trigger_type="time",
            name="past",
            created_by="test",
            created_at=now,
            conditions={"run_at": (now - timedelta(minutes=5)).isoformat()},
        )
    )
    assert await engine.next_wakeup(now) is None  # past-due handled by evaluate, not the alarm


@pytest.mark.asyncio
async def test_evaluate_tick_uses_stored_timezone_for_cron(
    fake_redis: Any, snapshot_dir: Path
) -> None:
    fake_redis.kv[USER_TIMEZONE_KEY] = "America/Denver"
    store = TriggerStore(redis=fake_redis, snapshot_dir=snapshot_dir)
    engine = TriggerEngine(store=store, redis=fake_redis)
    cls = TriggerRegistry.get("time")
    await store.save(
        cls(
            trigger_id="cron-denver",
            trigger_type="time",
            name="7am Denver",
            created_by="test",
            created_at=datetime(2026, 7, 16, 0, 0, tzinfo=UTC),
            conditions={"cron": "0 7 * * *"},
        )
    )
    await engine.evaluate_tick(datetime(2026, 7, 16, 12, 30, tzinfo=UTC))  # 6:30am Denver
    assert not fake_redis.streams.get(EVENTS_STREAM)  # not yet 7am local
    await engine.evaluate_tick(datetime(2026, 7, 16, 13, 0, 1, tzinfo=UTC))  # 7:00:01 Denver
    assert fake_redis.streams.get(EVENTS_STREAM)  # fired
```

(Imports for this file: `USER_TIMEZONE_KEY` from `shared.streams`, `EVENTS_STREAM` likewise, `timedelta`, plus whatever the file already imports.)

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest core/triggers/tests/test_engine.py -q` → FAIL (`next_wakeup` missing; cron test fires at UTC 7am).

- [ ] **Step 3: Implement in `core/triggers/engine.py`.** Add `from shared.usertime import get_user_timezone` to imports. Replace `evaluate_tick`/`evaluate_event` and add `next_wakeup`:

```python
    async def evaluate_tick(self, now: datetime) -> None:
        """Evaluate all enabled triggers against the current time (scheduler pass)."""
        tz = await get_user_timezone(self._redis)
        await self._evaluate_all(TriggerContext(now=now, tz=tz))

    async def evaluate_event(self, event: StateChangedEvent) -> None:
        """Evaluate all enabled triggers against an incoming event."""
        tz = await get_user_timezone(self._redis)
        await self._evaluate_all(TriggerContext(now=datetime.now(UTC), tz=tz, event=event))

    async def next_wakeup(self, now: datetime) -> datetime | None:
        """Earliest strictly-future clock candidate across enabled triggers.

        Past-due candidates are excluded on purpose: the scheduler evaluates
        before arming the alarm, so a past-due trigger either fired (and
        re-anchored) or is blocked on non-time conditions — in which case the
        event path, not the clock, will complete it.
        """
        tz = await get_user_timezone(self._redis)
        context = TriggerContext(now=now, tz=tz)
        result: datetime | None = None
        for trigger in await self._store.list_all(enabled_only=True):
            try:
                candidate = trigger.next_fire_time(context)
            except Exception as e:
                logger.error("next_fire_time failed for '%s': %s", trigger.trigger_id, e)
                continue
            if candidate is None or candidate <= now:
                continue
            if result is None or candidate < result:
                result = candidate
        return result
```

- [ ] **Step 4: Run to verify pass** — `.venv/bin/python -m pytest core/triggers/tests/ -q` → pass; mypy clean.

- [ ] **Step 5: Commit** — `git commit -am "feat(triggers): tz-aware evaluation contexts + next_wakeup"`

---

### Task 6: Scheduler loop replaces the 1s tick; wire `start_sync` in both processes

**Files:**
- Modify: `core/triggers/__main__.py` (replace `tick_loop` with `scheduler_loop`; start/stop sync)
- Modify: `core/conscious/__main__.py` (start/stop sync on its TriggerStore)
- Test: `core/triggers/tests/test_main.py` (append)

**Interfaces:**
- Consumes: `store.start_sync/stop_sync/add_on_change` (Task 4), `engine.next_wakeup` (Task 5).
- Produces: `scheduler_loop(engine: TriggerEngine, store: TriggerStore) -> None` in `core/triggers/__main__.py`.

- [ ] **Step 1: Write the failing test** — append to `core/triggers/tests/test_main.py`:

```python
@pytest.mark.asyncio
async def test_scheduler_fires_newly_created_reminder_within_a_second(
    fake_redis: Any, snapshot_dir: Path, tmp_path: Path
) -> None:
    """End-to-end latency regression: create in 'conscious' store, fire via
    'triggers' scheduler — no refresh(), no tick, no 60s window."""
    from core.triggers.__main__ import scheduler_loop

    triggers_store = TriggerStore(redis=fake_redis, snapshot_dir=snapshot_dir)
    conscious_store = TriggerStore(redis=fake_redis, snapshot_dir=tmp_path / "b")
    engine = TriggerEngine(store=triggers_store, redis=fake_redis)

    await triggers_store.start_sync()
    task = asyncio.create_task(scheduler_loop(engine, triggers_store))
    await asyncio.sleep(0.05)
    try:
        cls = TriggerRegistry.get("time")
        due = datetime.now(UTC) + timedelta(seconds=0.3)
        await conscious_store.save(
            cls(
                trigger_id="fast-reminder",
                trigger_type="time",
                name="fast reminder",
                created_by="test",
                created_at=datetime.now(UTC),
                one_shot=True,
                conditions={"run_at": due.isoformat()},
            )
        )
        await asyncio.sleep(1.0)
        fired = fake_redis.streams.get(EVENTS_STREAM, [])
        assert any("fast reminder" in e.get("event", "") for e in fired)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await triggers_store.stop_sync()
```

(Imports to add in the test file: `asyncio`, `contextlib`, `timedelta`, `EVENTS_STREAM`.)

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest core/triggers/tests/test_main.py -q` → FAIL (`scheduler_loop` missing).

- [ ] **Step 3: Implement.** In `core/triggers/__main__.py`, replace `tick_loop` with:

```python
async def scheduler_loop(engine: TriggerEngine, store: TriggerStore) -> None:
    """Event-driven clock: evaluate, then sleep until the earliest next fire
    time — woken instantly by any trigger mutation (pub/sub via the store).

    Replaces the 1s tick loop. Ordering matters: the wake event is cleared
    BEFORE evaluating/recomputing, so a mutation landing mid-pass leaves the
    event set and the wait returns immediately — re-arms are never missed.
    """
    wake = asyncio.Event()
    store.add_on_change(wake.set)

    while not _shutdown.is_set():
        wake.clear()
        try:
            await engine.evaluate_tick(datetime.now(UTC))
        except Exception as e:
            logger.error("Scheduler evaluation error: %s", e)
        try:
            next_due = await engine.next_wakeup(datetime.now(UTC))
        except Exception as e:
            logger.error("Scheduler next_wakeup error: %s", e)
            next_due = None
        timeout: float | None = None
        if next_due is not None:
            timeout = max((next_due - datetime.now(UTC)).total_seconds(), 0.0)
        try:
            await asyncio.wait_for(wake.wait(), timeout)
        except TimeoutError:
            pass
```

In `run()`: after `engine = TriggerEngine(...)` add `await store.start_sync()`; in the `tasks` list replace `asyncio.create_task(tick_loop(engine))` with `asyncio.create_task(scheduler_loop(engine, store))` (keep the 60s `_periodic(store.refresh, 60.0, "Cache refresh")` — reconciliation net, user decision); in the `finally` block add `await store.stop_sync()` before `await client.unregister()`. Also note the shutdown signal handler should wake the scheduler: in `_handle_signal` nothing changes — task cancellation in `finally` ends the loop.

In `core/conscious/__main__.py`: immediately after the `trigger_store = TriggerStore(...)` construction add `await trigger_store.start_sync()`, and add `await trigger_store.stop_sync()` in that process's shutdown/finally path (locate the existing cleanup block at the end of its `run()`; add the call alongside the other awaited cleanups).

- [ ] **Step 4: Run to verify pass** — `.venv/bin/python -m pytest core/triggers/tests/ -q` → pass (including any existing `test_main.py` tests that referenced `tick_loop` — update their imports/assertions to `scheduler_loop` if present). `mypy --strict core/` → clean.

- [ ] **Step 5: Commit** — `git commit -am "feat(triggers): scheduled-wakeup firing replaces 1s tick; wire store sync in both processes"`

---

### Task 7: Boundary normalization in TriggerFeature + prompt/tool docs

**Files:**
- Modify: `core/triggers/feature.py` (`create_trigger`, `update_trigger`)
- Modify: `core/conscious/prompts/personality.md` (run_at instruction)
- Test: `core/triggers/tests/test_feature.py` (append)

**Interfaces:**
- Consumes: `normalize_conditions` (Task 2), `get_user_timezone` (Task 1).
- Produces: naive `run_at` is localized to the user's timezone at write time — stored triggers are always tz-aware.

- [ ] **Step 1: Write the failing tests** — append to `core/triggers/tests/test_feature.py` (follow that file's existing fixture for building a `TriggerFeature` with a store; use `fake_redis` for the redis arg):

```python
@pytest.mark.asyncio
async def test_create_trigger_localizes_naive_run_at(
    fake_redis: Any, snapshot_dir: Path
) -> None:
    fake_redis.kv[USER_TIMEZONE_KEY] = "America/Denver"
    store = TriggerStore(redis=fake_redis, snapshot_dir=snapshot_dir)
    feature = TriggerFeature(TriggerFeatureContext(store=store, redis=fake_redis))
    result = await feature.create_trigger(
        name="tea time",
        trigger_type="time",
        conditions={"run_at": "2026-07-16T15:00:00"},
    )
    assert "error" not in result
    stored = await store.get(result["trigger_id"])
    assert stored is not None
    run_at = stored.conditions.run_at
    assert run_at.utcoffset() == timedelta(hours=-6)


@pytest.mark.asyncio
async def test_update_trigger_localizes_naive_run_at(
    fake_redis: Any, snapshot_dir: Path
) -> None:
    fake_redis.kv[USER_TIMEZONE_KEY] = "America/Denver"
    store = TriggerStore(redis=fake_redis, snapshot_dir=snapshot_dir)
    feature = TriggerFeature(TriggerFeatureContext(store=store, redis=fake_redis))
    created = await feature.create_trigger(
        name="tea time",
        trigger_type="time",
        conditions={"run_at": "2026-07-16T15:00:00+00:00"},
    )
    updated = await feature.update_trigger(
        trigger_id=created["trigger_id"],
        conditions={"run_at": "2026-07-16T16:00:00"},
    )
    assert "error" not in updated
    stored = await store.get(created["trigger_id"])
    assert stored is not None and stored.conditions.run_at.utcoffset() == timedelta(hours=-6)
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest core/triggers/tests/test_feature.py -q` → FAIL (naive stored as naive → tzinfo None).

- [ ] **Step 3: Implement.** In `core/triggers/feature.py` add imports `from shared.usertime import get_user_timezone`. In `create_trigger`, wrap construction:

```python
        try:
            tz_name = "UTC" if self._redis is None else await get_user_timezone(self._redis)
            normalized = cls.normalize_conditions(conditions, tz_name)
            trigger = cls(
                trigger_id=str(uuid4()),
                trigger_type=trigger_type,
                name=name,
                enabled=True,
                one_shot=one_shot,
                created_by="tool-call",
                created_at=datetime.now(UTC),
                action=validated_action,
                urgency=validated_urgency,
                conditions=normalized,
            )
        except Exception as e:
            return {"error": f"Invalid conditions for type '{trigger_type}': {e}"}
```

In `update_trigger`, inside the `if conditions is not None:` branch, before validating:

```python
        if conditions is not None:
            cls = TriggerRegistry.get(target.trigger_type)
            try:
                tz_name = "UTC" if self._redis is None else await get_user_timezone(self._redis)
                normalized = cls.normalize_conditions(conditions, tz_name)
                conditions_model = cls.Conditions  # type: ignore[attr-defined]
                validated = conditions_model(**normalized)
                updates["conditions"] = validated.model_dump(mode="json")
            except Exception as e:
                return {"error": f"Invalid conditions: {e}"}
```

In `core/conscious/prompts/personality.md`, replace the reminders line with:

```markdown
- For reminders, alarms, and scheduled tasks — use triggers.create_trigger with type "time" and a run_at timestamp in ISO-8601 WITH your current UTC offset (e.g. 2026-07-16T15:00:00-06:00), computed from the Current Time section.
```

- [ ] **Step 4: Run to verify pass** — `.venv/bin/python -m pytest core/triggers/tests/ -q` → pass; mypy clean.

- [ ] **Step 5: Commit** — `git commit -am "feat(triggers): localize naive run_at at the tool boundary; prompt offset instruction"`

---

### Task 8: `UserRequest.timezone` + web channel ingestion

**Files:**
- Modify: `bus/schemas/events.py` (`UserRequest`)
- Modify: `core/channels/web_server.py` (WS handler)
- Test: `tests/bus/test_events.py` or the existing bus schema test file (append one test); web ingestion covered by the pure-helper test below.

**Interfaces:**
- Consumes: `is_valid_timezone`, `set_user_timezone` (Task 1).
- Produces: `UserRequest.timezone: str | None = None`; module-level helper `_resolve_client_timezone(data: dict[str, Any]) -> str | None` in `web_server.py`. Task 9 reads `request.timezone`.

- [ ] **Step 1: Write the failing tests.** Append to the existing bus events test module (find it via `ls tests/bus/`):

```python
def test_user_request_timezone_optional_and_roundtrips() -> None:
    req = UserRequest(
        source="web-pwa",
        channel="web_pwa",
        session_id="s1",
        identity_claim="sir",
        content_type="text",
        content="hi",
    )
    assert req.timezone is None  # old clients unaffected
    req2 = UserRequest.model_validate_json(
        req.model_copy(update={"timezone": "America/Denver"}).model_dump_json()
    )
    assert req2.timezone == "America/Denver"
```

Create/append `tests/core/channels/test_web_timezone.py`:

```python
"""Tests for client timezone extraction in the web channel."""

from __future__ import annotations

from core.channels.web_server import _resolve_client_timezone


def test_resolves_valid_timezone() -> None:
    assert _resolve_client_timezone({"timezone": "America/Denver"}) == "America/Denver"


def test_rejects_invalid_or_missing() -> None:
    assert _resolve_client_timezone({"timezone": "Not/AZone"}) is None
    assert _resolve_client_timezone({"timezone": 42}) is None
    assert _resolve_client_timezone({}) is None
```

- [ ] **Step 2: Run to verify failure** — both test files FAIL.

- [ ] **Step 3: Implement.** In `bus/schemas/events.py`, add to `UserRequest` after `audio_ref`:

```python
    timezone: str | None = None  # IANA name from the client (e.g. America/Denver)
```

In `core/channels/web_server.py` add imports `from shared.usertime import is_valid_timezone, set_user_timezone` and a module-level helper:

```python
def _resolve_client_timezone(data: dict[str, Any]) -> str | None:
    """Validated IANA timezone from a client WS payload, else None."""
    tz = data.get("timezone")
    if isinstance(tz, str) and is_valid_timezone(tz):
        return tz
    return None
```

In the WS message loop, just above the `request = UserRequest(...)` construction:

```python
                client_tz = _resolve_client_timezone(data)
                if client_tz:
                    await set_user_timezone(r, client_tz)
```

and add `timezone=client_tz,` to the `UserRequest(...)` kwargs.

- [ ] **Step 4: Run to verify pass** — `.venv/bin/python -m pytest tests/bus/ tests/core/channels/ -q` → pass; `mypy --strict bus/ core/` → clean. (The sdk schema-compatibility suite runs in the full gate — optional fields are backward compatible.)

- [ ] **Step 5: Commit** — `git commit -am "feat(bus,channels): client timezone on UserRequest, persisted via web channel"`

---

### Task 9: Local wall-clock in the LLM prompt + tz-aware routine matching

**Files:**
- Modify: `core/conscious/context_assembler.py` (`assemble` gains `tz_name`)
- Modify: `core/conscious/engine.py` (`process_request` resolves tz; routine hint + `check_routine_suggestions` use local now)
- Modify: `core/librarian/consolidator.py` (pattern check in local tz)
- Test: `tests/core/conscious/test_context_assembler.py` (create if missing, else append)

**Interfaces:**
- Consumes: `request.timezone` (Task 8), `get_user_timezone`/`is_valid_timezone` (Task 1).
- Produces: `assemble(..., tz_name: str = "UTC")` — new keyword-only-position-compatible param appended after `content_type`.

- [ ] **Step 1: Write the failing tests** — `tests/core/conscious/test_context_assembler.py`:

```python
"""Tests for Current Time rendering in the system prompt."""

from __future__ import annotations

from datetime import UTC, datetime

from core.conscious.context_assembler import ContextAssembler
from core.conscious.identity import IdentityResult


def _assemble(tz_name: str) -> str:
    assembler = ContextAssembler()
    identity = IdentityResult(identity="sir", confidence=1.0)
    return assembler.assemble(
        identity=identity,
        tools_section="- t: tool",
        now=datetime(2026, 7, 16, 20, 5, 32, tzinfo=UTC),
        tz_name=tz_name,
    )


def test_current_time_rendered_in_user_timezone() -> None:
    prompt = _assemble("America/Denver")
    assert "## Current Time" in prompt
    assert "Thursday 2026-07-16T14:05:32-06:00 (America/Denver)" in prompt


def test_current_time_utc_fallback() -> None:
    prompt = _assemble("UTC")
    assert "Thursday 2026-07-16T20:05:32+00:00 (UTC)" in prompt
```

(If `IdentityResult` needs other required fields, mirror the construction used in existing conscious tests — check `tests/core/conscious/test_engine.py` for the pattern.)

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest tests/core/conscious/test_context_assembler.py -q` → FAIL.

- [ ] **Step 3: Implement.** `core/conscious/context_assembler.py`: add `from zoneinfo import ZoneInfo` import; add `tz_name: str = "UTC"` parameter to `assemble` (after `content_type`); replace the Current Time block:

```python
        # 2b. Current time (always — needed for time-based triggers/reminders)
        if now is not None:
            local = now.astimezone(ZoneInfo(tz_name))
            stamp = f"{local.strftime('%A')} {local.isoformat(timespec='seconds')}"
            parts.append(f"\n## Current Time\n{stamp} ({tz_name})")
```

`core/conscious/engine.py`: add `from zoneinfo import ZoneInfo` and `from shared.usertime import get_user_timezone, is_valid_timezone` imports. In `process_request`, after `now = datetime.now(UTC)`:

```python
        tz_name = (
            request.timezone
            if request.timezone and is_valid_timezone(request.timezone)
            else await get_user_timezone(self._redis)
        )
```

Pass `tz_name=tz_name` in the `self._assembler.assemble(...)` call, and change the routine hint call to use local wall-clock:

```python
                routine_hint = self._build_routine_hint(now.astimezone(ZoneInfo(tz_name)))
```

In `check_routine_suggestions`, where `now` is resolved (it defaults to UTC now), convert to local before matching:

```python
        tz_name = await get_user_timezone(self._redis)
        now = (now or datetime.now(UTC)).astimezone(ZoneInfo(tz_name))
```

`core/librarian/consolidator.py` line ~884 — convert once before the loop that calls `_check_pattern_fired` (place directly above the loop; the method is async and has `self._redis`):

```python
        from zoneinfo import ZoneInfo

        from shared.usertime import get_user_timezone

        local_now = now.astimezone(ZoneInfo(await get_user_timezone(self._redis)))
```

and change the call to `self._check_pattern_fired(routine, local_now)`. (Keep `now` for the `last_hit`/timestamp updates — only pattern matching goes local.)

- [ ] **Step 4: Run to verify pass** — `.venv/bin/python -m pytest tests/core/conscious/ tests/core/librarian/ -q` → pass; `mypy --strict core/` → clean.

- [ ] **Step 5: Commit** — `git commit -am "feat(conscious,librarian): local wall-clock prompt + tz-aware routine matching"`

---

### Task 10: Web frontend sends the client timezone

**Files:**
- Modify: `web/src/lib/chat-socket.ts`
- Test: `web/src/lib/chat-socket.test.ts` (create)

- [ ] **Step 1: Write the failing test** — `web/src/lib/chat-socket.test.ts`:

```ts
import { describe, expect, it, vi } from "vitest";

const { sent } = vi.hoisted(() => ({ sent: [] as Record<string, unknown>[] }));

vi.mock("./ws", () => {
  class ReconnectingSocket {
    onstatus: (s: unknown) => void = () => {};
    onopen: () => void = () => {};
    onmessage: (data: unknown) => void = () => {};
    connect(): void {}
    close(): void {}
    send(payload: Record<string, unknown>): boolean {
      sent.push(payload);
      return true;
    }
  }
  return { ReconnectingSocket };
});

import { ChatSocket } from "./chat-socket";

describe("ChatSocket payloads", () => {
  it("include the client IANA timezone", () => {
    const socket = new ChatSocket();
    socket.sendText("hello");
    const body = sent.at(-1)!;
    expect(body.timezone).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone);
    expect(body.channel).toBe("web_pwa");
  });
});
```

- [ ] **Step 2: Run to verify failure** — `cd web && npm run test` → new test FAILS (`timezone` undefined).

- [ ] **Step 3: Implement** — in `chat-socket.ts` `payload()`:

```ts
  private payload(type: "text" | "audio", content: string): Record<string, unknown> {
    const body: Record<string, unknown> = {
      type,
      content,
      channel: "web_pwa",
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    };
    if (!this.firstMessageSent && this.sessionId) body.session_id = this.sessionId;
    this.firstMessageSent = true;
    return body;
  }
```

- [ ] **Step 4: Run to verify pass** — `cd web && npm run lint && npm run test` → pass.

- [ ] **Step 5: Commit** — `git commit -am "feat(web): send client IANA timezone on every chat message"`

---

### Task 11: iOS — AlfredKit DTOs carry the timezone

**Repo:** `alfred-ios` (separate worktree + branch — do NOT touch the main checkout).

**Files:**
- Modify: `Packages/AlfredKit/Sources/AlfredKit/DTOs/ClientDTOs.swift`
- Test: `Packages/AlfredKit/Tests/AlfredKitTests/DTOs/DTORoundTripTests.swift` (append)

- [ ] **Step 1: Create the worktree**

```bash
git -C /Users/anirudhlath/code/private/alfred/alfred-ios worktree add \
  .claude/worktrees/client-timezone -b feat/client-timezone origin/master
cd /Users/anirudhlath/code/private/alfred/alfred-ios/.claude/worktrees/client-timezone
```

(If `.claude/worktrees` is not ignored in that repo, add it to `.git/info/exclude` first, as done for `alfred/`.)

- [ ] **Step 2: Write the failing tests** — append to `DTORoundTripTests.swift` (match its existing XCTest/Testing style):

```swift
    func testTextMessageIncludesTimezone() throws {
        let dto = TextMessageDTO(content: "hi", identity: "sir")
        let data = try JSONEncoder().encode(dto)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(json["timezone"] as? String, TimeZone.current.identifier)
    }

    func testAudioMessageIncludesTimezone() throws {
        let dto = AudioMessageDTO(content: "data:audio/aac;base64,AA==", identity: "sir")
        let data = try JSONEncoder().encode(dto)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(json["timezone"] as? String, TimeZone.current.identifier)
    }

    func testTextMessageDecodesWithoutTimezone() throws {
        let legacy = #"{"type":"text","content":"hi","identity":"sir","channel":"ios"}"#
        let dto = try JSONDecoder().decode(TextMessageDTO.self, from: Data(legacy.utf8))
        XCTAssertNil(dto.timezone)
    }
```

- [ ] **Step 3: Run to verify failure** — `swift test --package-path Packages/AlfredKit` → FAIL (no `timezone` member).

- [ ] **Step 4: Implement.** `TextMessageDTO`: add `public let timezone: String?`; init gains `timezone: String? = TimeZone.current.identifier` (assign it); `CodingKeys` gains `case timezone` (in the plain list); `encode` gains `try container.encodeIfPresent(timezone, forKey: .timezone)`. `AudioMessageDTO` (synthesized Codable): add `public let timezone: String?` and init param `timezone: String? = TimeZone.current.identifier`. Callers in `ClientMessage.swift` need no change — the default parameter supplies the device timezone.

- [ ] **Step 5: Run to verify pass** — `swift test --package-path Packages/AlfredKit` → pass.

- [ ] **Step 6: Commit + push**

```bash
git add -A && git commit -m "feat(AlfredKit): send device IANA timezone on text/audio messages

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push -u origin feat/client-timezone
```

---

### Task 12: Docs

**Files:**
- Modify: `docs/trigger-engine.md` — replace the 1s-tick description/diagram with the scheduled-wakeup + pub/sub coherence design; document `TRIGGERS_CHANGED_CHANNEL` message shapes (`saved`/`deleted`/`tz-changed`), the 60s reconciliation refresh, and `next_fire_time` semantics (computed cron, exactly-once catch-up).
- Modify: `docs/architecture.md` — add `alfred:user:timezone` + the coherence channel to the system description where Redis keys/streams are listed.
- Modify: `core/CLAUDE.md` — update the Trigger Engine data-flow mermaid (`Tick[1s Tick Loop]` → `Sched[Scheduled Wakeup<br/>next_fire_time + pub/sub re-arm]`) and add gotchas: "TriggerStore coherence is pub/sub (`alfred:triggers:changed`) — never mutate `alfred:triggers` without going through TriggerStore"; "User timezone lives at `alfred:user:timezone` via `shared/usertime.py` — resolution stored → `ALFRED_TIMEZONE` → UTC".
- Modify: `CLAUDE.md` (repo root) — Key Paths gains `shared/usertime.py`; add the same two gotchas to the root gotcha list.

- [ ] **Step 1:** Make the edits above (prose task — keep each addition to 1-3 lines, matching the docs' existing density).
- [ ] **Step 2:** Commit — `git commit -am "docs: scheduled-wakeup trigger engine + user timezone"`

---

### Task 13: Full gates + architect review

- [ ] **Step 1: Full verification in the `alfred` worktree**

```bash
ruff check . --fix && ruff format .
mypy --strict bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/
HF_HUB_OFFLINE=1 .venv/bin/python -m pytest -q
cd web && npm run lint && npm run test && npm run build && cd ..
```

Expected: 0 lint errors, all pytest green (946 baseline + new), frontend green. **mypy caveat:** fresh venvs hit the pre-existing redis-8.0.1/mypy-2.3 stub drift on master (backlog ticket `mypy-strict-redis8-stub-drift`, ~76 errors). The gate here is **zero NEW errors**: the recorded baseline at this branch's base (f3fcea3, this worktree's fresh venv) is **exactly 76 errors in 27 files** — the post-change count must not exceed 76, and files this branch touches must contribute zero of them. Fix anything new before proceeding.

- [ ] **Step 2: Architect review** — dispatch `feature-dev:code-architect` over the full diff (`git diff master...HEAD`). Fix every issue it raises. Also dispatch the project's `pillar-reviewer` and `schema-reviewer` agents (Five Pillars + `UserRequest` compat).
- [ ] **Step 3: Commit fixes** — one commit per review round.

---

### Task 14: Simplify, memory upkeep, QA backlog, PR

- [ ] **Step 1:** Run the `/simplify` skill over the branch diff; apply its findings; re-run the gates from Task 13 Step 1; commit.
- [ ] **Step 2:** Run `claude-md-management:claude-md-improver` to catch stale CLAUDE.md content introduced by this change; commit if it edits anything.
- [ ] **Step 3:** Dispatch a `general-purpose` subagent to write QA backlog tickets in `docs/qa-backlog/` for what automation can't verify, following the repo QA template. Minimum set: `reminder-5s-latency.md` (critical — live "remind me in 5 seconds", expect ≤ ~6s), `reminder-absolute-local-time.md` (web + iOS "remind me at <time>"), `cron-timezone-change.md` (cron re-arms after tz change). Commit.
- [ ] **Step 4:** Push and open PRs:
  - `alfred`: PR from `feat/instant-triggers-client-tz` → `master`, body summarizing both fixes + spec/plan links, ending with the standard generated-with footer.
  - `alfred-ios`: PR from `feat/client-timezone` → `master`.
- [ ] **Step 5:** Report PR URLs and the QA backlog items to the user.
