# D9 — Proactive Notification System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement proactive notification dispatch with DND awareness, priority routing, and multi-channel delivery (Signal, WebSocket, Voice).

**Architecture:** A deterministic `NotificationDispatcher` sits between `NotificationPublisher` and delivery channels. It checks DND state (manual Redis key + calendar), defers non-urgent notifications during DND, and routes to auto-discovered `ChannelAdapter` subclasses based on urgency. Deferred notifications drain via time triggers (no polling).

**Tech Stack:** Python 3.13+, async, Pydantic v2, Redis (streams + keys), pytest + pytest-asyncio, ruff, mypy --strict

**Spec:** `docs/superpowers/specs/2026-03-20-proactive-notifications-design.md`

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `core/notifications/schema.py` | `Urgency` enum + `Notification` model + `DNDStatus` model |
| `core/notifications/channels.py` | `ChannelAdapter` ABC + `ChannelRegistry` with `@register` decorator |
| `core/notifications/dnd.py` | `DNDChecker` — reads manual DND from Redis + calendar meeting check |
| `core/notifications/dispatcher.py` | `NotificationDispatcher` — DND check → defer or route to channels |
| `core/notifications/adapters/signal.py` | `SignalChannelAdapter` — wraps Signal Bridge send logic |
| `core/notifications/adapters/websocket.py` | `WebSocketChannelAdapter` — pushes to connected WS sessions |
| `core/notifications/adapters/voice.py` | `VoiceChannelAdapter` — TTS synthesis + WS audio push |
| `core/notifications/adapters/__init__.py` | Empty (package marker) |
| `tests/core/notifications/test_schema.py` | Schema validation tests |
| `tests/core/notifications/test_channels.py` | ChannelRegistry + adapter base tests |
| `tests/core/notifications/test_dnd.py` | DND checker tests (manual + calendar) |
| `tests/core/notifications/test_dispatcher.py` | Dispatcher routing + deferral + drain tests |
| `tests/core/notifications/test_adapters.py` | Concrete adapter delivery tests |
| `tests/core/notifications/test_integration.py` | End-to-end notification flow with Redis |

### Modified Files

| File | Change |
|------|--------|
| `bus/schemas/events.py` | Add `Urgency`, `Notification` re-exports for discoverability (canonical definitions in `core/notifications/schema.py`) |
| `shared/streams.py` | Add `DND_STATE_KEY` and `DEFERRED_NOTIFICATIONS_KEY` constants |
| `core/notifications/publisher.py` | Refactor `publish()` to accept `Notification` and call `Dispatcher.dispatch()` |
| `core/conscious/cost.py` | Update `send_alert_if_needed()` to use `Urgency.URGENT` enum |
| `core/channels/signal_bridge/bridge.py` | Extract send logic; `poll_notifications()` removed (Dispatcher handles routing) |
| `core/channels/web_server.py` | Expose session broadcast helper; handle `notification` + `voice_notification` message types |
| `core/conscious/__main__.py` | Wire `NotificationDispatcher` + `DNDChecker` into startup |
| `tests/core/notifications/test_publisher.py` | Update tests for new `publish()` signature |
| `tests/core/conscious/test_cost.py` | Update urgency string assertions |

---

## Task 1: Notification Schema + Stream Constants

**Files:**
- Create: `core/notifications/schema.py`
- Modify: `shared/streams.py:20` (add 2 constants)
- Test: `tests/core/notifications/test_schema.py`

- [ ] **Step 1: Write failing tests for Notification schema**

```python
# tests/core/notifications/test_schema.py
"""Tests for notification schema models."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from core.notifications.schema import DNDStatus, Notification, Urgency


class TestUrgency:
    def test_enum_values(self) -> None:
        assert Urgency.INFORMATIONAL == "informational"
        assert Urgency.IMPORTANT == "important"
        assert Urgency.URGENT == "urgent"

    def test_enum_from_string(self) -> None:
        assert Urgency("informational") is Urgency.INFORMATIONAL


class TestNotification:
    def test_minimal_creation(self) -> None:
        n = Notification(
            title="Test",
            body="Hello",
            urgency=Urgency.INFORMATIONAL,
            source="test",
        )
        assert n.title == "Test"
        assert n.notification_id  # auto-generated UUID
        assert n.timestamp  # auto-generated datetime

    def test_rejects_invalid_urgency(self) -> None:
        with pytest.raises(ValidationError):
            Notification(
                title="Test",
                body="Hello",
                urgency="invalid",  # type: ignore[arg-type]
                source="test",
            )

    def test_serialization_roundtrip(self) -> None:
        n = Notification(
            title="Budget",
            body="80% used",
            urgency=Urgency.URGENT,
            source="cost_tracker",
            timestamp=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        )
        data = n.model_dump_json()
        restored = Notification.model_validate_json(data)
        assert restored.title == n.title
        assert restored.urgency is Urgency.URGENT


class TestDNDStatus:
    def test_inactive_default(self) -> None:
        status = DNDStatus(active=False)
        assert not status.active
        assert status.reason is None
        assert status.source is None
        assert status.until is None

    def test_active_with_fields(self) -> None:
        status = DNDStatus(
            active=True,
            reason="User requested",
            source="manual",
            until=datetime(2026, 3, 20, 15, 0, tzinfo=UTC),
        )
        assert status.active
        assert status.source == "manual"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd alfred && uv run pytest tests/core/notifications/test_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.notifications.schema'`

- [ ] **Step 3: Implement schema module**

```python
# core/notifications/schema.py
"""Notification models — schema for the proactive notification system."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class Urgency(StrEnum):
    """Notification urgency level. Determines channel routing.

    Members are declared in ascending order so that list(Urgency) is ordered.
    Use Urgency.URGENT != notification.urgency for DND bypass checks.
    """

    INFORMATIONAL = "informational"
    IMPORTANT = "important"
    URGENT = "urgent"


class Notification(BaseModel):
    """A notification to be dispatched to one or more channels."""

    notification_id: str = Field(default_factory=lambda: str(uuid4()))
    title: str
    body: str
    urgency: Urgency
    source: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DNDStatus(BaseModel):
    """Current Do-Not-Disturb state."""

    active: bool
    reason: str | None = None
    source: str | None = None  # "manual" | "calendar"
    until: datetime | None = None
```

- [ ] **Step 4: Add stream constants**

In `shared/streams.py`, add after `NOTIFICATIONS_STREAM`:

```python
DND_STATE_KEY = "alfred:memory:dnd"
DEFERRED_NOTIFICATIONS_KEY = "alfred:notifications:deferred"
```

- [ ] **Step 5: Add re-exports to bus/schemas/events.py**

At the end of `bus/schemas/events.py`, add:

```python
# Re-export notification types for discoverability (canonical defs in core.notifications.schema)
from core.notifications.schema import Notification, Urgency  # noqa: F401
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd alfred && uv run pytest tests/core/notifications/test_schema.py -v`
Expected: all PASS

- [ ] **Step 7: Run linting + type check**

Run: `cd alfred && ruff check core/notifications/schema.py shared/streams.py bus/schemas/events.py && ruff format core/notifications/schema.py && mypy --strict core/notifications/schema.py`

- [ ] **Step 8: Commit**

```bash
cd alfred && git add core/notifications/schema.py shared/streams.py bus/schemas/events.py tests/core/notifications/test_schema.py
git commit -m "feat(d9): add Notification schema, Urgency enum, DNDStatus model + stream constants"
```

---

## Task 2: ChannelAdapter ABC + ChannelRegistry

**Files:**
- Create: `core/notifications/channels.py`
- Test: `tests/core/notifications/test_channels.py`

- [ ] **Step 1: Write failing tests for ChannelAdapter + ChannelRegistry**

```python
# tests/core/notifications/test_channels.py
"""Tests for ChannelAdapter ABC and ChannelRegistry."""

from __future__ import annotations

from typing import ClassVar

import pytest

from core.notifications.channels import ChannelAdapter, ChannelRegistry
from core.notifications.schema import Notification, Urgency


class FakeAdapter(ChannelAdapter):
    name: ClassVar[str] = "fake"
    supported_urgencies: ClassVar[set[Urgency]] = {Urgency.INFORMATIONAL, Urgency.IMPORTANT}

    def __init__(self) -> None:
        self.delivered: list[Notification] = []

    async def deliver(self, notification: Notification) -> None:
        self.delivered.append(notification)


class UrgentOnlyAdapter(ChannelAdapter):
    name: ClassVar[str] = "urgent_only"
    supported_urgencies: ClassVar[set[Urgency]] = {Urgency.URGENT}

    async def deliver(self, notification: Notification) -> None:
        pass


@pytest.fixture(autouse=True)
def _clear_registry() -> None:
    """Reset registry between tests."""
    ChannelRegistry._registry.clear()
    ChannelRegistry._instances.clear()


class TestChannelAdapter:
    def test_supports_urgency_true(self) -> None:
        adapter = FakeAdapter()
        assert adapter.supports_urgency(Urgency.INFORMATIONAL) is True

    def test_supports_urgency_false(self) -> None:
        adapter = FakeAdapter()
        assert adapter.supports_urgency(Urgency.URGENT) is False


class TestChannelRegistry:
    def test_register_decorator(self) -> None:
        @ChannelRegistry.register()
        class TestAdapter(ChannelAdapter):
            name: ClassVar[str] = "test"
            supported_urgencies: ClassVar[set[Urgency]] = {Urgency.INFORMATIONAL}

            async def deliver(self, notification: Notification) -> None:
                pass

        assert "test" in ChannelRegistry._registry

    def test_get_adapters_for_urgency(self) -> None:
        ChannelRegistry._registry["fake"] = FakeAdapter
        ChannelRegistry._registry["urgent_only"] = UrgentOnlyAdapter
        ChannelRegistry.set_instance("fake", FakeAdapter())
        ChannelRegistry.set_instance("urgent_only", UrgentOnlyAdapter())

        adapters = ChannelRegistry.get_adapters_for_urgency(Urgency.INFORMATIONAL)
        names = [type(a).name for a in adapters]
        assert "fake" in names
        assert "urgent_only" not in names

    def test_get_adapters_caches_instances(self) -> None:
        ChannelRegistry._registry["fake"] = FakeAdapter
        ChannelRegistry.set_instance("fake", FakeAdapter())
        adapters1 = ChannelRegistry.get_adapters_for_urgency(Urgency.INFORMATIONAL)
        adapters2 = ChannelRegistry.get_adapters_for_urgency(Urgency.INFORMATIONAL)
        assert adapters1[0] is adapters2[0]

    def test_uninitialized_adapter_skipped(self) -> None:
        """Registered but not initialized adapters are not returned."""
        ChannelRegistry._registry["fake"] = FakeAdapter
        # Don't call set_instance
        adapters = ChannelRegistry.get_adapters_for_urgency(Urgency.INFORMATIONAL)
        assert len(adapters) == 0

    def test_available_returns_names(self) -> None:
        ChannelRegistry._registry["fake"] = FakeAdapter
        assert "fake" in ChannelRegistry.available()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd alfred && uv run pytest tests/core/notifications/test_channels.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement ChannelAdapter + ChannelRegistry**

```python
# core/notifications/channels.py
"""Channel adapter base class and auto-discovery registry."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from core.notifications.schema import Notification, Urgency

logger = logging.getLogger(__name__)


class ChannelAdapter(ABC):
    """Base class for notification delivery channels."""

    name: ClassVar[str]
    supported_urgencies: ClassVar[set[Urgency]]

    def supports_urgency(self, urgency: Urgency) -> bool:
        """Check if this adapter handles the given urgency level."""
        return urgency in self.supported_urgencies

    @abstractmethod
    async def deliver(self, notification: Notification) -> None:
        """Deliver a notification through this channel."""
        ...


class ChannelRegistry:
    """Auto-discovery registry for channel adapters.

    Uses decorator-based registration (same pattern as IntegrationRegistry).
    """

    _registry: ClassVar[dict[str, type[ChannelAdapter]]] = {}
    _instances: ClassVar[dict[str, ChannelAdapter]] = {}

    @classmethod
    def register(cls, **kwargs: Any) -> Any:
        """Class decorator. Registers adapter at import time."""

        def decorator(adapter_cls: type[ChannelAdapter]) -> type[ChannelAdapter]:
            name = adapter_cls.name
            cls._registry[name] = adapter_cls
            logger.info("Registered channel adapter: %s", name)
            return adapter_cls

        return decorator

    @classmethod
    def get_adapters_for_urgency(cls, urgency: Urgency) -> list[ChannelAdapter]:
        """Return cached instances of all adapters supporting the given urgency.

        Only returns adapters that have been explicitly initialized via
        set_instance(). Adapters registered via @register but not yet
        initialized are skipped — this prevents silent failures from
        adapters constructed with no args before startup wiring completes.
        """
        result: list[ChannelAdapter] = []
        for name in cls._registry:
            if name not in cls._instances:
                logger.debug("Adapter '%s' registered but not initialized, skipping", name)
                continue
            instance = cls._instances[name]
            if instance.supports_urgency(urgency):
                result.append(instance)
        return result

    @classmethod
    def available(cls) -> list[str]:
        """Return all registered adapter names."""
        return list(cls._registry.keys())

    @classmethod
    def set_instance(cls, name: str, instance: ChannelAdapter) -> None:
        """Inject a pre-built adapter instance (for adapters needing constructor args)."""
        cls._instances[name] = instance
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd alfred && uv run pytest tests/core/notifications/test_channels.py -v`
Expected: all PASS

- [ ] **Step 5: Lint + type check**

Run: `cd alfred && ruff check core/notifications/channels.py && ruff format core/notifications/channels.py && mypy --strict core/notifications/channels.py`

- [ ] **Step 6: Commit**

```bash
cd alfred && git add core/notifications/channels.py tests/core/notifications/test_channels.py
git commit -m "feat(d9): add ChannelAdapter ABC + ChannelRegistry with decorator registration"
```

---

## Task 3: DNDChecker

**Files:**
- Create: `core/notifications/dnd.py`
- Test: `tests/core/notifications/test_dnd.py`

- [ ] **Step 1: Write failing tests for DNDChecker**

```python
# tests/core/notifications/test_dnd.py
"""Tests for DNDChecker — manual DND + calendar-based DND."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.notifications.dnd import DNDChecker
from shared.streams import DND_STATE_KEY


@pytest.fixture
def redis() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def checker(redis: AsyncMock) -> DNDChecker:
    return DNDChecker(redis=redis, calendar_adapter=None)


class TestManualDND:
    @pytest.mark.asyncio
    async def test_no_dnd_state_returns_inactive(self, checker: DNDChecker, redis: AsyncMock) -> None:
        redis.get.return_value = None
        status = await checker.is_active()
        assert not status.active

    @pytest.mark.asyncio
    async def test_active_dnd_returns_active(self, checker: DNDChecker, redis: AsyncMock) -> None:
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        redis.get.return_value = json.dumps({
            "active": True,
            "until": future,
            "reason": "User requested",
            "source": "manual",
        })
        status = await checker.is_active()
        assert status.active
        assert status.source == "manual"

    @pytest.mark.asyncio
    async def test_expired_dnd_returns_inactive_and_cleans_up(
        self, checker: DNDChecker, redis: AsyncMock
    ) -> None:
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        redis.get.return_value = json.dumps({
            "active": True,
            "until": past,
            "reason": "User requested",
            "source": "manual",
        })
        status = await checker.is_active()
        assert not status.active
        redis.delete.assert_called_once_with(DND_STATE_KEY)

    @pytest.mark.asyncio
    async def test_active_dnd_no_expiry(self, checker: DNDChecker, redis: AsyncMock) -> None:
        """DND with no 'until' field stays active indefinitely."""
        redis.get.return_value = json.dumps({
            "active": True,
            "reason": "Hold my calls",
            "source": "manual",
        })
        status = await checker.is_active()
        assert status.active


class TestCalendarDND:
    @pytest.mark.asyncio
    async def test_calendar_meeting_active(self, redis: AsyncMock) -> None:
        now = datetime.now(UTC)
        calendar = MagicMock()
        calendar.execute = AsyncMock(return_value=MagicMock(data={
            "events": [
                {
                    "summary": "Team standup",
                    "start": (now - timedelta(minutes=10)).isoformat(),
                    "end": (now + timedelta(minutes=20)).isoformat(),
                }
            ]
        }))
        checker = DNDChecker(redis=redis, calendar_adapter=calendar)
        redis.get.return_value = None  # No manual DND

        status = await checker.is_active()
        assert status.active
        assert status.source == "calendar"

    @pytest.mark.asyncio
    async def test_no_active_meeting(self, redis: AsyncMock) -> None:
        now = datetime.now(UTC)
        calendar = MagicMock()
        calendar.execute = AsyncMock(return_value=MagicMock(data={
            "events": [
                {
                    "summary": "Past meeting",
                    "start": (now - timedelta(hours=2)).isoformat(),
                    "end": (now - timedelta(hours=1)).isoformat(),
                }
            ]
        }))
        checker = DNDChecker(redis=redis, calendar_adapter=calendar)
        redis.get.return_value = None

        status = await checker.is_active()
        assert not status.active

    @pytest.mark.asyncio
    async def test_calendar_error_falls_through(self, redis: AsyncMock) -> None:
        """Calendar failure should not block notifications."""
        calendar = MagicMock()
        calendar.execute = AsyncMock(side_effect=Exception("CalDAV down"))
        checker = DNDChecker(redis=redis, calendar_adapter=calendar)
        redis.get.return_value = None

        status = await checker.is_active()
        assert not status.active

    @pytest.mark.asyncio
    async def test_manual_dnd_takes_priority_over_calendar(self, redis: AsyncMock) -> None:
        """Manual DND is checked first — calendar is skipped if manual is active."""
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        redis.get.return_value = json.dumps({
            "active": True,
            "until": future,
            "reason": "User requested",
            "source": "manual",
        })
        calendar = MagicMock()
        calendar.execute = AsyncMock()
        checker = DNDChecker(redis=redis, calendar_adapter=calendar)

        status = await checker.is_active()
        assert status.active
        assert status.source == "manual"
        # Calendar should NOT have been called
        calendar.execute.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd alfred && uv run pytest tests/core/notifications/test_dnd.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement DNDChecker**

```python
# core/notifications/dnd.py
"""DND (Do Not Disturb) state checker.

Checks manual DND (Redis key) first, then calendar meetings.
First match wins. Calendar errors are non-fatal.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from core.notifications.schema import DNDStatus
from shared.streams import DND_STATE_KEY

if TYPE_CHECKING:
    from core.integrations.base import Integration
    from shared.types import AioRedis

logger = logging.getLogger(__name__)


class DNDChecker:
    """Check Do-Not-Disturb state from manual override and calendar."""

    def __init__(
        self,
        redis: AioRedis,
        calendar_adapter: Integration | None,
    ) -> None:
        self._redis = redis
        self._calendar = calendar_adapter

    async def is_active(self) -> DNDStatus:
        """Check DND state. Manual override first, then calendar. First match wins."""
        # 1. Check manual DND
        manual = await self._check_manual()
        if manual.active:
            return manual

        # 2. Check calendar
        if self._calendar is not None:
            calendar = await self._check_calendar()
            if calendar.active:
                return calendar

        return DNDStatus(active=False)

    async def _check_manual(self) -> DNDStatus:
        """Check manual DND state in Redis."""
        raw: bytes | str | None = await self._redis.get(DND_STATE_KEY)  # type: ignore[misc]
        if raw is None:
            return DNDStatus(active=False)

        data: dict[str, Any] = json.loads(raw)
        if not data.get("active", False):
            return DNDStatus(active=False)

        # Check expiry
        until_str = data.get("until")
        if until_str is not None:
            until = datetime.fromisoformat(until_str)
            if until.tzinfo is None:
                until = until.replace(tzinfo=UTC)
            if datetime.now(UTC) >= until:
                # Expired — clean up
                await self._redis.delete(DND_STATE_KEY)  # type: ignore[misc]
                logger.info("DND expired, cleaned up state")
                return DNDStatus(active=False)
        else:
            until = None

        return DNDStatus(
            active=True,
            reason=data.get("reason"),
            source=data.get("source", "manual"),
            until=until,
        )

    async def _check_calendar(self) -> DNDStatus:
        """Check if user is in an active calendar meeting."""
        assert self._calendar is not None
        try:
            from core.integrations.base import IntegrationRequest

            result = await self._calendar.execute(
                IntegrationRequest(action="get_today_events", params={})
            )
            events: list[dict[str, Any]] = result.data.get("events", [])
            now = datetime.now(UTC)

            for event in events:
                start = datetime.fromisoformat(event["start"])
                end = datetime.fromisoformat(event["end"])
                if start.tzinfo is None:
                    start = start.replace(tzinfo=UTC)
                if end.tzinfo is None:
                    end = end.replace(tzinfo=UTC)
                if start <= now < end:
                    return DNDStatus(
                        active=True,
                        reason=f"In meeting: {event.get('summary', 'Unknown')}",
                        source="calendar",
                        until=end,
                    )
        except Exception as exc:
            logger.warning("Calendar DND check failed (non-fatal): %s", exc)

        return DNDStatus(active=False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd alfred && uv run pytest tests/core/notifications/test_dnd.py -v`
Expected: all PASS

- [ ] **Step 5: Lint + type check**

Run: `cd alfred && ruff check core/notifications/dnd.py && ruff format core/notifications/dnd.py && mypy --strict core/notifications/dnd.py`

- [ ] **Step 6: Commit**

```bash
cd alfred && git add core/notifications/dnd.py tests/core/notifications/test_dnd.py
git commit -m "feat(d9): add DNDChecker with manual Redis + calendar meeting detection"
```

---

## Task 4: NotificationDispatcher

**Files:**
- Create: `core/notifications/dispatcher.py`
- Test: `tests/core/notifications/test_dispatcher.py`

- [ ] **Step 1: Write failing tests for NotificationDispatcher**

```python
# tests/core/notifications/test_dispatcher.py
"""Tests for NotificationDispatcher — routing, deferral, and drain logic."""

from __future__ import annotations

import json
from typing import ClassVar
from unittest.mock import AsyncMock

import pytest

from core.notifications.channels import ChannelAdapter, ChannelRegistry
from core.notifications.dispatcher import NotificationDispatcher
from core.notifications.dnd import DNDChecker
from core.notifications.schema import DNDStatus, Notification, Urgency
from shared.streams import DEFERRED_NOTIFICATIONS_KEY


class FakeSignal(ChannelAdapter):
    name: ClassVar[str] = "fake_signal"
    supported_urgencies: ClassVar[set[Urgency]] = {
        Urgency.INFORMATIONAL,
        Urgency.IMPORTANT,
        Urgency.URGENT,
    }

    def __init__(self) -> None:
        self.delivered: list[Notification] = []

    async def deliver(self, notification: Notification) -> None:
        self.delivered.append(notification)


class FakeWS(ChannelAdapter):
    name: ClassVar[str] = "fake_ws"
    supported_urgencies: ClassVar[set[Urgency]] = {Urgency.IMPORTANT, Urgency.URGENT}

    def __init__(self) -> None:
        self.delivered: list[Notification] = []

    async def deliver(self, notification: Notification) -> None:
        self.delivered.append(notification)


@pytest.fixture(autouse=True)
def _clear_registry() -> None:
    ChannelRegistry._registry.clear()
    ChannelRegistry._instances.clear()


@pytest.fixture
def redis() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def dnd_inactive() -> DNDChecker:
    checker = AsyncMock(spec=DNDChecker)
    checker.is_active.return_value = DNDStatus(active=False)
    return checker


@pytest.fixture
def dnd_active() -> DNDChecker:
    checker = AsyncMock(spec=DNDChecker)
    checker.is_active.return_value = DNDStatus(active=True, source="manual", reason="User requested")
    return checker


def _make_notification(urgency: Urgency = Urgency.INFORMATIONAL) -> Notification:
    return Notification(title="Test", body="Hello", urgency=urgency, source="test")


class TestDispatchRouting:
    @pytest.mark.asyncio
    async def test_informational_routes_to_signal_only(
        self, redis: AsyncMock, dnd_inactive: DNDChecker
    ) -> None:
        signal = FakeSignal()
        ws = FakeWS()
        ChannelRegistry._registry["fake_signal"] = FakeSignal
        ChannelRegistry._instances["fake_signal"] = signal
        ChannelRegistry._registry["fake_ws"] = FakeWS
        ChannelRegistry._instances["fake_ws"] = ws

        dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd_inactive)
        await dispatcher.dispatch(_make_notification(Urgency.INFORMATIONAL))

        assert len(signal.delivered) == 1
        assert len(ws.delivered) == 0

    @pytest.mark.asyncio
    async def test_important_routes_to_signal_and_ws(
        self, redis: AsyncMock, dnd_inactive: DNDChecker
    ) -> None:
        signal = FakeSignal()
        ws = FakeWS()
        ChannelRegistry._registry["fake_signal"] = FakeSignal
        ChannelRegistry._instances["fake_signal"] = signal
        ChannelRegistry._registry["fake_ws"] = FakeWS
        ChannelRegistry._instances["fake_ws"] = ws

        dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd_inactive)
        await dispatcher.dispatch(_make_notification(Urgency.IMPORTANT))

        assert len(signal.delivered) == 1
        assert len(ws.delivered) == 1


class TestDNDDeferral:
    @pytest.mark.asyncio
    async def test_dnd_active_defers_informational(
        self, redis: AsyncMock, dnd_active: DNDChecker
    ) -> None:
        signal = FakeSignal()
        ChannelRegistry._registry["fake_signal"] = FakeSignal
        ChannelRegistry._instances["fake_signal"] = signal

        dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd_active)
        await dispatcher.dispatch(_make_notification(Urgency.INFORMATIONAL))

        assert len(signal.delivered) == 0
        redis.rpush.assert_called_once()
        call_args = redis.rpush.call_args[0]
        assert call_args[0] == DEFERRED_NOTIFICATIONS_KEY

    @pytest.mark.asyncio
    async def test_dnd_active_delivers_urgent(
        self, redis: AsyncMock, dnd_active: DNDChecker
    ) -> None:
        signal = FakeSignal()
        ChannelRegistry._registry["fake_signal"] = FakeSignal
        ChannelRegistry._instances["fake_signal"] = signal

        dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd_active)
        await dispatcher.dispatch(_make_notification(Urgency.URGENT))

        assert len(signal.delivered) == 1
        redis.rpush.assert_not_called()


class TestDrainDeferred:
    @pytest.mark.asyncio
    async def test_drain_resubmits_through_dispatch(
        self, redis: AsyncMock, dnd_inactive: DNDChecker
    ) -> None:
        signal = FakeSignal()
        ChannelRegistry._registry["fake_signal"] = FakeSignal
        ChannelRegistry._instances["fake_signal"] = signal

        notification = _make_notification(Urgency.INFORMATIONAL)
        serialized = notification.model_dump_json()

        # Simulate: first lrange returns 1 item, rpop returns that item, then None
        redis.llen.return_value = 1
        redis.lpop.side_effect = [serialized.encode(), None]

        dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd_inactive)
        await dispatcher.drain_deferred()

        assert len(signal.delivered) == 1

    @pytest.mark.asyncio
    async def test_drain_empty_queue_is_noop(
        self, redis: AsyncMock, dnd_inactive: DNDChecker
    ) -> None:
        dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd_inactive)
        redis.llen.return_value = 0
        await dispatcher.drain_deferred()
        redis.lpop.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd alfred && uv run pytest tests/core/notifications/test_dispatcher.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement NotificationDispatcher**

```python
# core/notifications/dispatcher.py
"""NotificationDispatcher — deterministic routing with DND awareness.

No LLM calls. Checks DND → defers or routes to channels by urgency.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from core.notifications.channels import ChannelRegistry
from core.notifications.schema import Notification, Urgency
from shared.streams import DEFERRED_NOTIFICATIONS_KEY

if TYPE_CHECKING:
    from core.notifications.dnd import DNDChecker
    from shared.types import AioRedis

logger = logging.getLogger(__name__)


class NotificationDispatcher:
    """Route notifications to channels, respecting DND state."""

    def __init__(self, redis: AioRedis, dnd_checker: DNDChecker) -> None:
        self._redis = redis
        self._dnd = dnd_checker

    async def dispatch(self, notification: Notification) -> None:
        """Route a notification to appropriate channels, respecting DND."""
        dnd_status = await self._dnd.is_active()

        if dnd_status.active and notification.urgency != Urgency.URGENT:
            # Defer non-urgent notifications during DND
            await self._redis.rpush(  # type: ignore[misc]
                DEFERRED_NOTIFICATIONS_KEY,
                notification.model_dump_json(),
            )
            logger.info(
                "Deferred notification '%s' (urgency=%s, DND source=%s)",
                notification.title,
                notification.urgency,
                dnd_status.source,
            )
            return

        await self._deliver(notification)

    async def drain_deferred(self) -> None:
        """Drain all deferred notifications through the dispatcher.

        Called when DND expires (via time trigger). Each notification is
        re-dispatched — if DND is still somehow active, it will re-defer.
        """
        count: int = await self._redis.llen(DEFERRED_NOTIFICATIONS_KEY)  # type: ignore[misc]
        if count == 0:
            return

        logger.info("Draining %d deferred notifications", count)
        while True:
            raw: bytes | str | None = await self._redis.lpop(  # type: ignore[misc]
                DEFERRED_NOTIFICATIONS_KEY
            )
            if raw is None:
                break
            notification = Notification.model_validate_json(raw)
            await self._deliver(notification)

    async def _deliver(self, notification: Notification) -> None:
        """Deliver notification to all matching channel adapters in parallel."""
        adapters = ChannelRegistry.get_adapters_for_urgency(notification.urgency)
        if not adapters:
            logger.warning(
                "No channel adapters registered for urgency=%s", notification.urgency
            )
            return

        results = await asyncio.gather(
            *(adapter.deliver(notification) for adapter in adapters),
            return_exceptions=True,
        )
        for adapter, result in zip(adapters, results):
            if isinstance(result, Exception):
                logger.error(
                    "Channel %s failed to deliver '%s': %s",
                    type(adapter).name,
                    notification.title,
                    result,
                )
            else:
                logger.info(
                    "Delivered '%s' via %s", notification.title, type(adapter).name
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd alfred && uv run pytest tests/core/notifications/test_dispatcher.py -v`
Expected: all PASS

- [ ] **Step 5: Lint + type check**

Run: `cd alfred && ruff check core/notifications/dispatcher.py && ruff format core/notifications/dispatcher.py && mypy --strict core/notifications/dispatcher.py`

- [ ] **Step 6: Commit**

```bash
cd alfred && git add core/notifications/dispatcher.py tests/core/notifications/test_dispatcher.py
git commit -m "feat(d9): add NotificationDispatcher with DND deferral and parallel channel delivery"
```

---

## Task 5: Concrete Channel Adapters

**Files:**
- Create: `core/notifications/adapters/__init__.py`
- Create: `core/notifications/adapters/signal.py`
- Create: `core/notifications/adapters/websocket.py`
- Create: `core/notifications/adapters/voice.py`
- Test: `tests/core/notifications/test_adapters.py`

- [ ] **Step 1: Write failing tests for all three adapters**

```python
# tests/core/notifications/test_adapters.py
"""Tests for concrete channel adapters: Signal, WebSocket, Voice."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.notifications.channels import ChannelRegistry
from core.notifications.schema import Notification, Urgency


@pytest.fixture(autouse=True)
def _clear_registry() -> None:
    ChannelRegistry._registry.clear()
    ChannelRegistry._instances.clear()


def _make_notification(urgency: Urgency = Urgency.IMPORTANT) -> Notification:
    return Notification(title="Test", body="Hello world", urgency=urgency, source="test")


class TestSignalChannelAdapter:
    @pytest.mark.asyncio
    async def test_delivers_formatted_message(self) -> None:
        from core.notifications.adapters.signal import SignalChannelAdapter

        bridge = AsyncMock()
        adapter = SignalChannelAdapter(bridge=bridge, recipient="+15551234567")

        notification = _make_notification()
        await adapter.deliver(notification)

        bridge.send_notification.assert_called_once_with("+15551234567", "Test: Hello world")

    def test_supports_all_urgencies(self) -> None:
        from core.notifications.adapters.signal import SignalChannelAdapter

        adapter = SignalChannelAdapter(bridge=AsyncMock(), recipient="+15551234567")
        assert adapter.supports_urgency(Urgency.INFORMATIONAL)
        assert adapter.supports_urgency(Urgency.IMPORTANT)
        assert adapter.supports_urgency(Urgency.URGENT)


class TestWebSocketChannelAdapter:
    @pytest.mark.asyncio
    async def test_delivers_to_connected_sessions(self) -> None:
        from core.notifications.adapters.websocket import WebSocketChannelAdapter

        ws1 = AsyncMock()
        ws2 = AsyncMock()
        session_getter = MagicMock(return_value=[ws1, ws2])

        adapter = WebSocketChannelAdapter(get_sessions=session_getter)
        await adapter.deliver(_make_notification())

        ws1.send_json.assert_called_once()
        ws2.send_json.assert_called_once()
        payload = ws1.send_json.call_args[0][0]
        assert payload["type"] == "notification"
        assert payload["title"] == "Test"

    @pytest.mark.asyncio
    async def test_silently_skips_when_no_sessions(self) -> None:
        from core.notifications.adapters.websocket import WebSocketChannelAdapter

        session_getter = MagicMock(return_value=[])
        adapter = WebSocketChannelAdapter(get_sessions=session_getter)
        # Should not raise
        await adapter.deliver(_make_notification())

    @pytest.mark.asyncio
    async def test_handles_send_failure_gracefully(self) -> None:
        from core.notifications.adapters.websocket import WebSocketChannelAdapter

        ws = AsyncMock()
        ws.send_json.side_effect = Exception("Connection closed")
        session_getter = MagicMock(return_value=[ws])

        adapter = WebSocketChannelAdapter(get_sessions=session_getter)
        # Should not raise — errors are logged
        await adapter.deliver(_make_notification())

    def test_supports_important_and_urgent(self) -> None:
        from core.notifications.adapters.websocket import WebSocketChannelAdapter

        adapter = WebSocketChannelAdapter(get_sessions=MagicMock(return_value=[]))
        assert not adapter.supports_urgency(Urgency.INFORMATIONAL)
        assert adapter.supports_urgency(Urgency.IMPORTANT)
        assert adapter.supports_urgency(Urgency.URGENT)


class TestVoiceChannelAdapter:
    @pytest.mark.asyncio
    async def test_synthesizes_and_pushes_audio(self) -> None:
        from core.notifications.adapters.voice import VoiceChannelAdapter

        tts = MagicMock()
        tts.synthesize.return_value = b"\x00\x01\x02\x03"  # Fake WAV bytes
        ws = AsyncMock()
        session_getter = MagicMock(return_value=[ws])

        adapter = VoiceChannelAdapter(get_tts=lambda: tts, get_sessions=session_getter)
        await adapter.deliver(_make_notification(Urgency.URGENT))

        tts.synthesize.assert_called_once_with("Test: Hello world")
        ws.send_json.assert_called_once()
        payload = ws.send_json.call_args[0][0]
        assert payload["type"] == "voice_notification"
        assert payload["audio"] == base64.b64encode(b"\x00\x01\x02\x03").decode()

    @pytest.mark.asyncio
    async def test_skips_when_tts_unavailable(self) -> None:
        from core.notifications.adapters.voice import VoiceChannelAdapter

        adapter = VoiceChannelAdapter(get_tts=lambda: None, get_sessions=MagicMock(return_value=[]))
        # Should not raise
        await adapter.deliver(_make_notification(Urgency.URGENT))

    @pytest.mark.asyncio
    async def test_skips_when_no_sessions(self) -> None:
        from core.notifications.adapters.voice import VoiceChannelAdapter

        tts = MagicMock()
        adapter = VoiceChannelAdapter(get_tts=lambda: tts, get_sessions=MagicMock(return_value=[]))
        await adapter.deliver(_make_notification(Urgency.URGENT))
        tts.synthesize.assert_not_called()

    def test_supports_urgent_only(self) -> None:
        from core.notifications.adapters.voice import VoiceChannelAdapter

        adapter = VoiceChannelAdapter(get_tts=lambda: None, get_sessions=MagicMock(return_value=[]))
        assert not adapter.supports_urgency(Urgency.INFORMATIONAL)
        assert not adapter.supports_urgency(Urgency.IMPORTANT)
        assert adapter.supports_urgency(Urgency.URGENT)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd alfred && uv run pytest tests/core/notifications/test_adapters.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create adapters package**

```python
# core/notifications/adapters/__init__.py
"""Channel adapter implementations."""
```

- [ ] **Step 4: Implement SignalChannelAdapter**

```python
# core/notifications/adapters/signal.py
"""Signal channel adapter — delivers notifications via Signal Bridge."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

from core.notifications.channels import ChannelAdapter, ChannelRegistry
from core.notifications.schema import Notification, Urgency

if TYPE_CHECKING:
    from core.channels.signal_bridge.bridge import SignalBridge

logger = logging.getLogger(__name__)


@ChannelRegistry.register()
class SignalChannelAdapter(ChannelAdapter):
    """Deliver notifications via Signal messenger."""

    name: ClassVar[str] = "signal"
    supported_urgencies: ClassVar[set[Urgency]] = {
        Urgency.INFORMATIONAL,
        Urgency.IMPORTANT,
        Urgency.URGENT,
    }

    def __init__(
        self,
        bridge: SignalBridge | None = None,
        recipient: str = "",
    ) -> None:
        self._bridge = bridge
        self._recipient = recipient

    async def deliver(self, notification: Notification) -> None:
        """Format and send notification via Signal Bridge."""
        if self._bridge is None:
            logger.warning("SignalChannelAdapter: no bridge configured, skipping")
            return
        message = f"{notification.title}: {notification.body}"
        await self._bridge.send_notification(self._recipient, message)
```

- [ ] **Step 5: Implement WebSocketChannelAdapter**

```python
# core/notifications/adapters/websocket.py
"""WebSocket channel adapter — pushes notifications to connected web sessions."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, ClassVar

from core.notifications.channels import ChannelAdapter, ChannelRegistry
from core.notifications.schema import Notification, Urgency

logger = logging.getLogger(__name__)


@ChannelRegistry.register()
class WebSocketChannelAdapter(ChannelAdapter):
    """Push notification JSON to all connected WebSocket sessions."""

    name: ClassVar[str] = "websocket"
    supported_urgencies: ClassVar[set[Urgency]] = {Urgency.IMPORTANT, Urgency.URGENT}

    def __init__(self, get_sessions: Callable[[], list[Any]] | None = None) -> None:
        self._get_sessions = get_sessions

    async def deliver(self, notification: Notification) -> None:
        """Push notification to all active WebSocket connections."""
        if self._get_sessions is None:
            logger.debug("WebSocketChannelAdapter: no session getter, skipping")
            return
        sessions = self._get_sessions()
        if not sessions:
            logger.debug("WebSocketChannelAdapter: no active sessions, skipping")
            return

        payload = {
            "type": "notification",
            "title": notification.title,
            "body": notification.body,
            "urgency": notification.urgency.value,
        }
        for ws in sessions:
            try:
                await ws.send_json(payload)
            except Exception as exc:
                logger.warning("Failed to push notification to WebSocket: %s", exc)
```

- [ ] **Step 6: Implement VoiceChannelAdapter**

```python
# core/notifications/adapters/voice.py
"""Voice channel adapter — TTS synthesis + WebSocket audio push."""

from __future__ import annotations

import base64
import logging
from collections.abc import Callable
from typing import Any, ClassVar

from core.notifications.channels import ChannelAdapter, ChannelRegistry
from core.notifications.schema import Notification, Urgency

logger = logging.getLogger(__name__)


@ChannelRegistry.register()
class VoiceChannelAdapter(ChannelAdapter):
    """Synthesize notification text to audio and push via WebSocket."""

    name: ClassVar[str] = "voice"
    supported_urgencies: ClassVar[set[Urgency]] = {Urgency.URGENT}

    def __init__(
        self,
        get_tts: Callable[[], Any | None] | None = None,
        get_sessions: Callable[[], list[Any]] | None = None,
    ) -> None:
        self._get_tts = get_tts
        self._get_sessions = get_sessions

    async def deliver(self, notification: Notification) -> None:
        """Synthesize text to audio and push to WebSocket sessions."""
        if self._get_sessions is None:
            return
        sessions = self._get_sessions()
        if not sessions:
            logger.debug("VoiceChannelAdapter: no active sessions, skipping")
            return

        if self._get_tts is None:
            return
        tts = self._get_tts()
        if tts is None:
            logger.debug("VoiceChannelAdapter: TTS not available, skipping")
            return

        text = f"{notification.title}: {notification.body}"
        try:
            wav_bytes: bytes = tts.synthesize(text)
        except Exception as exc:
            logger.error("VoiceChannelAdapter: TTS synthesis failed: %s", exc)
            return

        audio_b64 = base64.b64encode(wav_bytes).decode()
        payload = {
            "type": "voice_notification",
            "title": notification.title,
            "audio": audio_b64,
        }
        for ws in sessions:
            try:
                await ws.send_json(payload)
            except Exception as exc:
                logger.warning("Failed to push voice notification to WebSocket: %s", exc)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd alfred && uv run pytest tests/core/notifications/test_adapters.py -v`
Expected: all PASS

- [ ] **Step 8: Lint + type check**

Run: `cd alfred && ruff check core/notifications/adapters/ && ruff format core/notifications/adapters/ && mypy --strict core/notifications/adapters/`

- [ ] **Step 9: Commit**

```bash
cd alfred && git add core/notifications/adapters/ tests/core/notifications/test_adapters.py
git commit -m "feat(d9): add Signal, WebSocket, and Voice channel adapters with auto-registration"
```

---

## Task 6: Refactor NotificationPublisher + CostTracker + Startup Wiring

**Note:** The publisher refactor changes its constructor signature, so we MUST also update the conscious engine startup in the same task to keep every commit green.

**Files:**
- Modify: `core/notifications/publisher.py` (full rewrite)
- Modify: `core/conscious/cost.py:128-148` (urgency enum migration)
- Modify: `core/conscious/__main__.py:94` (wire new publisher constructor)
- Modify: `tests/core/notifications/test_publisher.py` (update for new API)
- Modify: `tests/core/conscious/test_cost.py` (update urgency assertions)

- [ ] **Step 1: Write updated publisher tests**

```python
# tests/core/notifications/test_publisher.py — FULL REWRITE
"""Tests for NotificationPublisher."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.notifications.publisher import NotificationPublisher
from core.notifications.schema import Notification, Urgency


@pytest.fixture
def dispatcher() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def publisher(dispatcher: AsyncMock) -> NotificationPublisher:
    return NotificationPublisher(dispatcher=dispatcher)


class TestPublish:
    @pytest.mark.asyncio
    async def test_publish_calls_dispatcher(
        self, publisher: NotificationPublisher, dispatcher: AsyncMock
    ) -> None:
        await publisher.publish(
            title="Budget Warning",
            body="80% consumed",
            urgency=Urgency.URGENT,
            source="cost_tracker",
        )
        dispatcher.dispatch.assert_called_once()
        notification = dispatcher.dispatch.call_args[0][0]
        assert isinstance(notification, Notification)
        assert notification.title == "Budget Warning"
        assert notification.urgency is Urgency.URGENT

    @pytest.mark.asyncio
    async def test_publish_default_urgency(
        self, publisher: NotificationPublisher, dispatcher: AsyncMock
    ) -> None:
        await publisher.publish(
            title="Info",
            body="FYI",
            source="test",
        )
        notification = dispatcher.dispatch.call_args[0][0]
        assert notification.urgency is Urgency.INFORMATIONAL
```

- [ ] **Step 2: Run tests to see them fail with old publisher**

Run: `cd alfred && uv run pytest tests/core/notifications/test_publisher.py -v`
Expected: FAIL — constructor signature mismatch

- [ ] **Step 3: Rewrite NotificationPublisher**

```python
# core/notifications/publisher.py — FULL REWRITE
"""NotificationPublisher — creates Notification objects and routes through Dispatcher."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.notifications.schema import Notification, Urgency

if TYPE_CHECKING:
    from core.notifications.dispatcher import NotificationDispatcher

logger = logging.getLogger(__name__)


class NotificationPublisher:
    """Creates and publishes notifications through the Dispatcher.

    This is the public API that other components (CostTracker, Conscious Engine,
    etc.) call to send notifications. The Dispatcher handles DND + channel routing.
    """

    def __init__(self, dispatcher: NotificationDispatcher) -> None:
        self._dispatcher = dispatcher

    async def publish(
        self,
        title: str,
        body: str,
        source: str,
        urgency: Urgency = Urgency.INFORMATIONAL,
    ) -> None:
        """Create a Notification and route it through the dispatcher."""
        notification = Notification(
            title=title,
            body=body,
            urgency=urgency,
            source=source,
        )
        await self._dispatcher.dispatch(notification)
        logger.info("Published notification: %s — %s (urgency=%s)", source, title, urgency)
```

- [ ] **Step 4: Run publisher tests to verify they pass**

Run: `cd alfred && uv run pytest tests/core/notifications/test_publisher.py -v`
Expected: all PASS

- [ ] **Step 5: Update CostTracker to use Urgency enum**

In `core/conscious/cost.py`, change `send_alert_if_needed()` (around line 137):

Old:
```python
await self._notifier.publish(
    channel="cost_alert",
    title="Budget Warning",
    body=f"Daily spend ${state.spend_usd:.2f} has reached 80% of ${state.cap_usd:.2f} cap",
    urgency="high",
)
```

New:
```python
await self._notifier.publish(
    title="Budget Warning",
    body=f"Daily spend ${state.spend_usd:.2f} has reached 80% of ${state.cap_usd:.2f} cap",
    source="cost_tracker",
    urgency=Urgency.URGENT,
)
```

Add import at top of `cost.py`:
```python
from core.notifications.schema import Urgency
```

- [ ] **Step 6: Update CostTracker tests**

In `tests/core/conscious/test_cost.py`, update any assertions that reference the old `channel` or `urgency="high"` parameters to match the new signature. The `notifier` mock needs to be an `AsyncMock` that accepts the new kwargs.

- [ ] **Step 7: Update `core/conscious/__main__.py` to wire new publisher constructor**

The old code (line 94-95):
```python
notifier = NotificationPublisher(redis=r)
```

Must become (using a temporary passthrough dispatcher until Task 8 wires the real one):
```python
from core.notifications.dispatcher import NotificationDispatcher
from core.notifications.dnd import DNDChecker

dnd_checker = DNDChecker(redis=r, calendar_adapter=None)
dispatcher = NotificationDispatcher(redis=r, dnd_checker=dnd_checker)
notifier = NotificationPublisher(dispatcher=dispatcher)
```

Also update the import from `from core.notifications.publisher import NotificationPublisher` (already present) and add new imports.

- [ ] **Step 8: Run all affected tests**

Run: `cd alfred && uv run pytest tests/core/notifications/ tests/core/conscious/test_cost.py -v`
Expected: all PASS

- [ ] **Step 9: Lint + type check**

Run: `cd alfred && ruff check core/notifications/publisher.py core/conscious/cost.py core/conscious/__main__.py && ruff format core/notifications/publisher.py core/conscious/cost.py core/conscious/__main__.py && mypy --strict core/notifications/publisher.py core/conscious/cost.py`

- [ ] **Step 10: Commit**

```bash
cd alfred && git add core/notifications/publisher.py core/conscious/cost.py core/conscious/__main__.py tests/core/notifications/test_publisher.py tests/core/conscious/test_cost.py
git commit -m "refactor(d9): NotificationPublisher routes through Dispatcher; CostTracker uses Urgency enum"
```

---

## Task 7: Signal Bridge Refactor

**Files:**
- Modify: `core/channels/signal_bridge/bridge.py` (remove `poll_notifications()`)
- Modify: tests for signal bridge as needed

The Signal Bridge's `poll_notifications()` method currently reads from `NOTIFICATIONS_STREAM` directly. With the Dispatcher routing to `SignalChannelAdapter`, this polling loop is no longer needed.

- [ ] **Step 1: Read current signal bridge tests**

Read `tests/core/channels/` to understand what tests exist for the bridge.

- [ ] **Step 2: Remove `poll_notifications()` from SignalBridge**

In `core/channels/signal_bridge/bridge.py`:
- Remove `poll_notifications()` method (lines 91-103)
- Remove `ensure_consumer_group()` method (lines 80-89) — no longer needed for notifications
- Remove `NOTIFICATIONS_STREAM` from imports (line 12) if no longer used
- Keep `_send_signal()`, `send_notification()`, `forward_inbound()`, `poll_responses()` intact

- [ ] **Step 3: Update signal bridge entry point if needed**

Check `core/channels/signal_bridge/__main__.py` or wherever the polling loop is called. Remove the `poll_notifications()` call from the main loop — the Dispatcher now handles delivery via `SignalChannelAdapter`.

- [ ] **Step 4: Run signal bridge tests**

Run: `cd alfred && uv run pytest tests/core/channels/ -v`
Expected: all PASS (or update tests that reference removed methods)

- [ ] **Step 5: Lint + type check**

Run: `cd alfred && ruff check core/channels/signal_bridge/ && mypy --strict core/channels/signal_bridge/`

- [ ] **Step 6: Commit**

```bash
cd alfred && git add core/channels/signal_bridge/
git commit -m "refactor(d9): remove notification polling from SignalBridge — Dispatcher handles delivery"
```

---

## Task 8: Wire Dispatcher into Conscious Engine Startup

**Files:**
- Modify: `core/conscious/__main__.py:55-154` (wire new components)
- Modify: `core/channels/__main__.py` (expose WS sessions for adapters)

- [ ] **Step 1: Read current channels entry point**

Read `core/channels/__main__.py` to understand how the web server + signal bridge start.

- [ ] **Step 2: Update `core/conscious/__main__.py` to wire Dispatcher**

In `core/conscious/__main__.py`, after creating the Redis connection and before creating the NotificationPublisher:

```python
# Import adapter modules to trigger @ChannelRegistry.register() decorators
import core.notifications.adapters.signal  # noqa: F401
import core.notifications.adapters.voice  # noqa: F401
import core.notifications.adapters.websocket  # noqa: F401
from core.notifications.channels import ChannelRegistry
from core.notifications.dispatcher import NotificationDispatcher
from core.notifications.dnd import DNDChecker
from core.notifications.schema import Urgency

# Try to get calendar adapter for DND checks (optional)
calendar_adapter = None
try:
    from core.integrations.registry import IntegrationRegistry
    calendar_adapter = IntegrationRegistry.get("apple_calendar")
except KeyError:
    logger.info("Calendar adapter not available — DND calendar checks disabled")

dnd_checker = DNDChecker(redis=r, calendar_adapter=calendar_adapter)
dispatcher = NotificationDispatcher(redis=r, dnd_checker=dnd_checker)

# Inject pre-built adapter instances that need constructor args
signal_bridge = SignalBridge(redis=r, phone_number=config.signal_phone_number)
from core.notifications.adapters.signal import SignalChannelAdapter
ChannelRegistry.set_instance(
    "signal",
    SignalChannelAdapter(bridge=signal_bridge, recipient=config.signal_phone_number),
)

notifier = NotificationPublisher(dispatcher=dispatcher)
cost_tracker = CostTracker(redis=r, daily_cap_usd=config.daily_cost_cap_usd, notifier=notifier)
```

- [ ] **Step 3: Expose WebSocket session list from web_server**

In `core/channels/web_server.py`, add a module-level list of active WebSocket connections that adapters can query:

```python
# At module level
_active_websockets: list[WebSocket] = []

def get_active_websockets() -> list[WebSocket]:
    """Return list of currently connected WebSocket sessions."""
    return list(_active_websockets)
```

In `websocket_endpoint()`, add/remove from the list:
```python
_active_websockets.append(websocket)
try:
    # ... existing handler loop ...
finally:
    _active_websockets.remove(websocket)
```

Wire into adapters in the channels entry point:
```python
from core.channels.web_server import get_active_websockets
from core.notifications.adapters.websocket import WebSocketChannelAdapter
from core.notifications.adapters.voice import VoiceChannelAdapter
from core.notifications.channels import ChannelRegistry

ChannelRegistry.set_instance("websocket", WebSocketChannelAdapter(get_sessions=get_active_websockets))
ChannelRegistry.set_instance("voice", VoiceChannelAdapter(get_tts=_get_tts, get_sessions=get_active_websockets))
```

- [ ] **Step 4: Run full test suite**

Run: `cd alfred && uv run pytest -x -v`
Expected: all PASS

- [ ] **Step 5: Lint + type check modified files**

Run: `cd alfred && ruff check core/conscious/__main__.py core/channels/web_server.py && ruff format core/conscious/__main__.py core/channels/web_server.py && mypy --strict core/conscious/__main__.py core/channels/web_server.py`

- [ ] **Step 6: Commit**

```bash
cd alfred && git add core/conscious/__main__.py core/channels/web_server.py core/channels/__main__.py
git commit -m "feat(d9): wire NotificationDispatcher + DNDChecker + channel adapters into startup"
```

---

## Task 9: Integration Test — End-to-End Notification Flow

**Files:**
- Create: `tests/core/notifications/test_integration.py`

- [ ] **Step 1: Write integration tests**

```python
# tests/core/notifications/test_integration.py
"""Integration tests for the full notification pipeline.

Tests the flow: publish → dispatcher → DND check → channel delivery / deferral → drain.
Uses mock Redis but real Dispatcher, DNDChecker, and ChannelRegistry wiring.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import ClassVar
from unittest.mock import AsyncMock

import pytest

from core.notifications.channels import ChannelAdapter, ChannelRegistry
from core.notifications.dispatcher import NotificationDispatcher
from core.notifications.dnd import DNDChecker
from core.notifications.publisher import NotificationPublisher
from core.notifications.schema import Notification, Urgency
from shared.streams import DEFERRED_NOTIFICATIONS_KEY, DND_STATE_KEY


class RecordingAdapter(ChannelAdapter):
    """Test adapter that records all deliveries."""

    name: ClassVar[str] = "recording"
    supported_urgencies: ClassVar[set[Urgency]] = {
        Urgency.INFORMATIONAL,
        Urgency.IMPORTANT,
        Urgency.URGENT,
    }

    def __init__(self) -> None:
        self.delivered: list[Notification] = []

    async def deliver(self, notification: Notification) -> None:
        self.delivered.append(notification)


@pytest.fixture(autouse=True)
def _clear_registry() -> None:
    ChannelRegistry._registry.clear()
    ChannelRegistry._instances.clear()


@pytest.fixture
def redis() -> AsyncMock:
    r = AsyncMock()
    r.get.return_value = None  # No DND by default
    r.llen.return_value = 0
    return r


@pytest.fixture
def adapter() -> RecordingAdapter:
    a = RecordingAdapter()
    ChannelRegistry._registry["recording"] = RecordingAdapter
    ChannelRegistry._instances["recording"] = a
    return a


class TestEndToEndFlow:
    @pytest.mark.asyncio
    async def test_publish_delivers_when_no_dnd(
        self, redis: AsyncMock, adapter: RecordingAdapter
    ) -> None:
        dnd = DNDChecker(redis=redis, calendar_adapter=None)
        dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd)
        publisher = NotificationPublisher(dispatcher=dispatcher)

        await publisher.publish(
            title="Weather Alert",
            body="Rain expected at 3pm",
            source="weather",
            urgency=Urgency.INFORMATIONAL,
        )

        assert len(adapter.delivered) == 1
        assert adapter.delivered[0].title == "Weather Alert"

    @pytest.mark.asyncio
    async def test_publish_defers_during_dnd(
        self, redis: AsyncMock, adapter: RecordingAdapter
    ) -> None:
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        redis.get.return_value = json.dumps({
            "active": True,
            "until": future,
            "reason": "Focus time",
            "source": "manual",
        })

        dnd = DNDChecker(redis=redis, calendar_adapter=None)
        dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd)
        publisher = NotificationPublisher(dispatcher=dispatcher)

        await publisher.publish(
            title="FYI",
            body="Non-urgent info",
            source="test",
            urgency=Urgency.INFORMATIONAL,
        )

        assert len(adapter.delivered) == 0
        redis.rpush.assert_called_once()

    @pytest.mark.asyncio
    async def test_urgent_bypasses_dnd(
        self, redis: AsyncMock, adapter: RecordingAdapter
    ) -> None:
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        redis.get.return_value = json.dumps({
            "active": True,
            "until": future,
            "reason": "Focus time",
            "source": "manual",
        })

        dnd = DNDChecker(redis=redis, calendar_adapter=None)
        dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd)
        publisher = NotificationPublisher(dispatcher=dispatcher)

        await publisher.publish(
            title="URGENT",
            body="Fire alarm",
            source="safety",
            urgency=Urgency.URGENT,
        )

        assert len(adapter.delivered) == 1

    @pytest.mark.asyncio
    async def test_drain_delivers_deferred(
        self, redis: AsyncMock, adapter: RecordingAdapter
    ) -> None:
        """Simulate: DND was active, deferred 2 notifications. DND expires, drain fires."""
        n1 = Notification(title="N1", body="First", urgency=Urgency.INFORMATIONAL, source="a")
        n2 = Notification(title="N2", body="Second", urgency=Urgency.IMPORTANT, source="b")

        redis.get.return_value = None  # DND now inactive
        redis.llen.return_value = 2
        redis.lpop.side_effect = [
            n1.model_dump_json().encode(),
            n2.model_dump_json().encode(),
            None,
        ]

        dnd = DNDChecker(redis=redis, calendar_adapter=None)
        dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd)
        await dispatcher.drain_deferred()

        assert len(adapter.delivered) == 2
        assert adapter.delivered[0].title == "N1"
        assert adapter.delivered[1].title == "N2"
```

- [ ] **Step 2: Run integration tests**

Run: `cd alfred && uv run pytest tests/core/notifications/test_integration.py -v`
Expected: all PASS

- [ ] **Step 3: Run full test suite to check nothing is broken**

Run: `cd alfred && uv run pytest -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
cd alfred && git add tests/core/notifications/test_integration.py
git commit -m "test(d9): add end-to-end integration tests for notification pipeline"
```

---

## Task 10: Drain Trigger Wiring — DND Expiry Fires `drain_deferred()`

The spec requires that deferred notifications are drained when DND expires, using the trigger system (no polling). Two paths:

1. **Manual DND:** When the Conscious Engine sets DND with an `until` time, it also creates a one-shot time trigger at that time. The trigger fires an `ActionRequest` that calls `drain_deferred()`.
2. **Calendar DND:** When the Dispatcher defers due to a meeting, it creates a one-shot time trigger at the meeting end time (with idempotency — skip if trigger already exists for that end time).

**Files:**
- Create: `core/notifications/drain_action.py` — action handler for drain requests
- Modify: `core/notifications/dispatcher.py` — create calendar drain trigger on deferral
- Test: `tests/core/notifications/test_drain_action.py`

- [ ] **Step 1: Write failing tests for DrainDeferredAction**

```python
# tests/core/notifications/test_drain_action.py
"""Tests for the drain-deferred action handler."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.notifications.drain_action import handle_drain_deferred


@pytest.mark.asyncio
async def test_handle_drain_deferred_calls_dispatcher() -> None:
    dispatcher = AsyncMock()
    await handle_drain_deferred(dispatcher)
    dispatcher.drain_deferred.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd alfred && uv run pytest tests/core/notifications/test_drain_action.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement drain action handler**

```python
# core/notifications/drain_action.py
"""Action handler for draining deferred notifications.

Called by the Trigger Engine when a DND-expiry time trigger fires.
The trigger's ActionRequest targets this handler.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.notifications.dispatcher import NotificationDispatcher

logger = logging.getLogger(__name__)


async def handle_drain_deferred(dispatcher: NotificationDispatcher) -> None:
    """Drain all deferred notifications. Called when DND expiry trigger fires."""
    logger.info("DND expiry trigger fired — draining deferred notifications")
    await dispatcher.drain_deferred()
```

- [ ] **Step 4: Add calendar drain trigger creation to Dispatcher**

In `core/notifications/dispatcher.py`, when deferring due to calendar DND (i.e., `dnd_status.source == "calendar"` and `dnd_status.until` is set), create a one-shot time trigger for the meeting end time. Add this method:

```python
async def _ensure_drain_trigger(self, drain_at: datetime) -> None:
    """Create a one-shot time trigger to drain deferred notifications.

    Idempotent: skips if a drain trigger for this time already exists.
    Uses the trigger store directly (injected at construction).
    """
    if self._trigger_store is None:
        logger.debug("No trigger store — skipping drain trigger creation")
        return

    trigger_id = f"drain-deferred-{int(drain_at.timestamp())}"
    existing = await self._trigger_store.get(trigger_id)
    if existing is not None:
        return  # Already scheduled

    from core.triggers.types.time import TimeTrigger

    trigger = TimeTrigger(
        trigger_id=trigger_id,
        name=f"Drain deferred notifications at {drain_at.isoformat()}",
        one_shot=True,
        action=ActionPayload(
            target_service="conscious-engine",
            tool_name="drain_deferred_notifications",
            parameters={},
        ),
        conditions=TimeTrigger.Conditions(run_at=drain_at),
    )
    await self._trigger_store.save(trigger)
    logger.info("Created drain trigger %s for %s", trigger_id, drain_at)
```

Update the constructor to optionally accept `trigger_store`:
```python
def __init__(
    self,
    redis: AioRedis,
    dnd_checker: DNDChecker,
    trigger_store: TriggerStore | None = None,
) -> None:
```

Call `_ensure_drain_trigger` in `dispatch()` when deferring with a known end time:
```python
if dnd_status.until is not None:
    await self._ensure_drain_trigger(dnd_status.until)
```

- [ ] **Step 5: Wire drain trigger store in `core/conscious/__main__.py`**

Pass the trigger store to the dispatcher:
```python
dispatcher = NotificationDispatcher(
    redis=r, dnd_checker=dnd_checker, trigger_store=trigger_store
)
```

- [ ] **Step 6: Wire drain action in the conscious engine's action dispatch**

In the conscious engine's request loop (or a dedicated action handler), when an ActionRequest with `tool_name="drain_deferred_notifications"` arrives, call `handle_drain_deferred(dispatcher)`. This can be done by adding it as a recognized internal action in the engine's tool dispatch or by processing it in the Trigger Engine's fire path.

The simplest approach: in `core/conscious/__main__.py`'s main loop, after processing user requests, also listen for drain actions on `ACTIONS_STREAM` or handle them via TriggerEngine's fire → ActionRequest path (which already publishes to `ACTIONS_STREAM`). The Conscious Engine already processes `ActionRequest` events via its tool dispatch — add `drain_deferred_notifications` as a system-level tool.

- [ ] **Step 7: Run tests**

Run: `cd alfred && uv run pytest tests/core/notifications/ -v`
Expected: all PASS

- [ ] **Step 8: Lint + type check**

Run: `cd alfred && ruff check core/notifications/drain_action.py core/notifications/dispatcher.py && mypy --strict core/notifications/drain_action.py core/notifications/dispatcher.py`

- [ ] **Step 9: Commit**

```bash
cd alfred && git add core/notifications/drain_action.py core/notifications/dispatcher.py core/conscious/__main__.py tests/core/notifications/test_drain_action.py
git commit -m "feat(d9): add drain trigger wiring — DND expiry creates one-shot trigger to drain deferred"
```

---

## Task 11: Documentation + Final Validation

**Files:**
- Create: `docs/notifications.md`
- Modify: `docs/architecture.md` (add notification system to diagrams)
- Modify: `docs/backlog/remaining-work.md` (mark D9 done)

- [ ] **Step 1: Write notification system documentation**

Create `docs/notifications.md` with:
- Architecture overview with Mermaid diagram showing the flow
- Data models (Urgency, Notification, DNDStatus)
- Channel adapter matrix (urgency → channels)
- DND behavior (manual, calendar, deferred drain)
- Redis keys used
- How to add a new channel adapter

- [ ] **Step 2: Update `docs/architecture.md`**

Add the notification system to the system-level Mermaid diagram showing:
```
NotificationPublisher → Dispatcher → DND Check → Channel Adapters (Signal, WebSocket, Voice)
```

- [ ] **Step 3: Update backlog**

In `docs/backlog/remaining-work.md`, mark D9 as DONE with a brief summary.

- [ ] **Step 4: Run full validation**

```bash
cd alfred
ruff check .
ruff format .
mypy --strict core/notifications/
uv run pytest -v
```

Expected: all checks pass, all tests green.

- [ ] **Step 5: Commit docs**

```bash
cd alfred && git add docs/notifications.md docs/architecture.md docs/backlog/remaining-work.md
git commit -m "docs(d9): add notification system documentation, update architecture and backlog"
```

---

## Summary

| Task | What | Tests |
|------|------|-------|
| 1 | Notification schema + stream constants | 7 |
| 2 | ChannelAdapter ABC + ChannelRegistry | 6 |
| 3 | DNDChecker (manual + calendar) | 7 |
| 4 | NotificationDispatcher | 6 |
| 5 | Signal, WebSocket, Voice adapters | 9 |
| 6 | Refactor Publisher + CostTracker + startup wiring | 4 |
| 7 | Signal Bridge refactor | 0 (existing tests updated) |
| 8 | Full channel adapter wiring | 0 (wiring, tested by integration) |
| 9 | Integration tests | 4 |
| 10 | Drain trigger wiring (DND expiry) | 1 |
| 11 | Documentation + validation | 0 |
| **Total** | | **~44 new tests** |
