# core/triggers/tests/test_engine.py
"""Tests for TriggerEngine — tick loop, event listener, and fire logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path  # noqa: TC003
from typing import Any
from unittest.mock import AsyncMock

import pytest

from core.triggers.models import ActionPayload, TriggerContext
from core.triggers.registry import TriggerRegistry
from core.triggers.store import TriggerStore
from shared.streams import EVENTS_STREAM, USER_TIMEZONE_KEY


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


@pytest.fixture
def snapshot_dir(tmp_path: Path) -> Path:
    d = tmp_path / "triggers"
    d.mkdir()
    return d


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
        # Must precede the boundary being evaluated — computed next_fire_time
        # anchors on created_at, so `datetime.now(UTC)` would postdate this
        # fixed historical tick and never fire.
        created_at=datetime(2026, 3, 10, 6, 0, 0, tzinfo=UTC),
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


@pytest.mark.asyncio
async def test_fire_default_fired_by_is_engine(
    mock_store: AsyncMock, mock_redis: AsyncMock
) -> None:
    import json

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
    await engine.fire(trigger, TriggerContext(now=datetime.now(UTC)))

    event_json = json.loads(mock_redis.xadd.call_args[0][1]["event"])
    assert event_json["fired_by"] == "engine"


@pytest.mark.asyncio
async def test_fire_admin_sets_fired_by_on_trigger_fired(
    mock_store: AsyncMock, mock_redis: AsyncMock
) -> None:
    import json

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
    await engine.fire(trigger, TriggerContext(now=datetime.now(UTC)), fired_by="admin")

    stream = mock_redis.xadd.call_args[0][0]
    event_json = json.loads(mock_redis.xadd.call_args[0][1]["event"])
    assert stream == "alfred:events"
    assert event_json["fired_by"] == "admin"


@pytest.mark.asyncio
async def test_next_wakeup_returns_earliest_future_candidate(
    fake_redis: Any, snapshot_dir: Path
) -> None:
    from core.triggers.engine import TriggerEngine

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
async def test_next_wakeup_excludes_past_due_and_none(fake_redis: Any, snapshot_dir: Path) -> None:
    from core.triggers.engine import TriggerEngine

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
    from core.triggers.engine import TriggerEngine

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
