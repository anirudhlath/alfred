# core/triggers/tests/test_engine.py
"""Tests for TriggerEngine — tick loop, event listener, and fire logic."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from core.triggers.models import ActionPayload, TriggerContext
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
async def test_fire_one_shot_deletes_trigger(mock_store: AsyncMock, mock_redis: AsyncMock) -> None:
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
async def test_fire_updates_last_fired(mock_store: AsyncMock, mock_redis: AsyncMock) -> None:
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

    mock_redis.xadd.assert_called()


@pytest.mark.asyncio
async def test_evaluate_event_fires_matching_sensor_trigger(
    mock_store: AsyncMock, mock_redis: AsyncMock
) -> None:
    from bus.schemas.events import StateChangedEvent
    from core.triggers.engine import TriggerEngine

    cls = TriggerRegistry.get("sensor")
    trigger = cls(
        trigger_id="t-1",
        trigger_type="sensor",
        name="tv watcher",
        created_by="test",
        created_at=datetime.now(UTC),
        conditions={"entity_id": "media_player.tv", "state_match": "on"},
    )
    mock_store.list_all = AsyncMock(return_value=[trigger])

    engine = TriggerEngine(store=mock_store, redis=mock_redis)
    event = StateChangedEvent(
        source="test",
        domain="home",
        entity_id="media_player.tv",
        new_state="on",
    )
    await engine.evaluate_event(event)

    mock_redis.xadd.assert_called()


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
