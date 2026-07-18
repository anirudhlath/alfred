"""Tests for the Trigger Engine entry point."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from bus.schemas.events import StateChangedEvent
from core.triggers.engine import TriggerEngine
from core.triggers.registry import TriggerRegistry
from core.triggers.store import TriggerStore
from shared.streams import EVENTS_STREAM, HOME_STATE_STREAM

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TriggerRegistry, "_registry", {})
    import core.triggers.types  # noqa: F401


@pytest.fixture
def snapshot_dir(tmp_path: Path) -> Path:
    d = tmp_path / "triggers"
    d.mkdir()
    return d


def test_main_is_importable() -> None:
    import core.triggers.__main__  # noqa: F401


def test_main_function_exists() -> None:
    from core.triggers.__main__ import main

    assert callable(main)


@pytest.mark.asyncio
async def test_scheduler_loop_evaluates_and_rearms() -> None:
    """Scheduler pass evaluates, computes the next wakeup, then waits on the
    store-change event — replacing the old 1s tick. Verifying add_on_change is
    wired to the wake event guards the instant re-arm this task delivers.
    """
    from core.triggers.__main__ import _shutdown, scheduler_loop

    callbacks: list[Callable[[], None]] = []
    mock_store = MagicMock()
    mock_store.add_on_change = MagicMock(side_effect=callbacks.append)

    mock_engine = AsyncMock()

    async def fake_eval(now: datetime) -> None:
        # End the loop after one pass and wake the waiter so wait_for returns
        # immediately (no real sleep) — the loop top then sees _shutdown set.
        _shutdown.set()
        for cb in callbacks:
            cb()

    mock_engine.evaluate_tick = AsyncMock(side_effect=fake_eval)
    mock_engine.next_wakeup = AsyncMock(return_value=None)

    _shutdown.clear()
    await scheduler_loop(mock_engine, mock_store)
    _shutdown.clear()

    mock_engine.evaluate_tick.assert_awaited_once()
    mock_engine.next_wakeup.assert_awaited_once()
    # The scheduler must have subscribed its wake event to store mutations.
    mock_store.add_on_change.assert_called_once()


class _FakeEventRedis:
    """Fake Redis serving one batch of stream entries, then signalling shutdown."""

    def __init__(self, entries: list[tuple[str, dict[str, str]]]) -> None:
        self._entries = entries
        self.read_streams: list[dict[str, str]] = []
        self.read_groups: list[str] = []
        self.acked: list[tuple[str, str, str]] = []
        self.groups_created: list[tuple[str, str]] = []

    async def xgroup_create(self, stream: str, group: str, **kwargs: Any) -> None:
        self.groups_created.append((stream, group))

    async def xreadgroup(
        self,
        group: str,
        consumer: str,
        streams: dict[str, str],
        count: int,
        block: int,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        from core.triggers.__main__ import _shutdown

        self.read_groups.append(group)
        self.read_streams.append(dict(streams))
        _shutdown.set()
        return [(next(iter(streams)), self._entries)]

    async def xack(self, stream: str, group: str, entry_id: str) -> None:
        self.acked.append((stream, group, entry_id))


@pytest.mark.asyncio
async def test_event_loop_consumes_home_state_stream(
    tv_on_event: StateChangedEvent,
) -> None:
    """Sensor evaluation must read real state changes from HOME_STATE_STREAM.

    Regression guard: home state changes are published to alfred:home:state_changed
    (by the MQTT bridge), NOT to alfred:events — consuming the wrong stream means
    sensor triggers never fire.
    """
    from core.triggers.__main__ import GROUP, _shutdown, event_loop

    redis = _FakeEventRedis([("1-0", {"event": tv_on_event.model_dump_json()})])
    engine = AsyncMock()

    _shutdown.clear()
    await event_loop(engine, redis)  # type: ignore[arg-type]
    _shutdown.clear()

    assert redis.groups_created == [(HOME_STATE_STREAM, GROUP)]
    assert all(HOME_STATE_STREAM in streams for streams in redis.read_streams)
    engine.evaluate_event.assert_awaited_once()
    parsed = engine.evaluate_event.await_args.args[0]
    assert isinstance(parsed, StateChangedEvent)
    assert parsed.entity_id == tv_on_event.entity_id
    assert redis.acked == [(HOME_STATE_STREAM, GROUP, "1-0")]


@pytest.mark.asyncio
async def test_event_loop_acks_and_warns_on_invalid_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Malformed stream entries are acked, skipped, and logged (not silent)."""
    from core.triggers.__main__ import GROUP, _shutdown, event_loop

    redis = _FakeEventRedis([("1-0", {"event": "not-json"})])
    engine = AsyncMock()

    _shutdown.clear()
    with caplog.at_level("WARNING", logger="core.triggers.__main__"):
        await event_loop(engine, redis)  # type: ignore[arg-type]
    _shutdown.clear()

    engine.evaluate_event.assert_not_awaited()
    assert redis.acked == [(HOME_STATE_STREAM, GROUP, "1-0")]
    assert any("1-0" in rec.getMessage() for rec in caplog.records)


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
