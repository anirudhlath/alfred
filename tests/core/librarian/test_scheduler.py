"""Tests for Librarian periodic scheduling."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from core.librarian.scheduler import LibrarianScheduler


@pytest.mark.asyncio
async def test_scheduler_calls_consolidate() -> None:
    """Scheduler should call consolidate() on the interval."""
    mock_librarian = AsyncMock()
    mock_librarian.consolidate = AsyncMock(return_value={"entries_processed": 0})

    scheduler = LibrarianScheduler(librarian=mock_librarian, interval_seconds=0.01)

    task = asyncio.create_task(scheduler.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert mock_librarian.consolidate.call_count >= 1


@pytest.mark.asyncio
async def test_scheduler_survives_consolidation_error() -> None:
    """Scheduler should keep running if consolidate() raises."""
    mock_librarian = AsyncMock()
    call_count = 0

    async def failing_then_ok() -> dict[str, int]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("LLM unavailable")
        return {"entries_processed": 0}

    mock_librarian.consolidate = failing_then_ok

    scheduler = LibrarianScheduler(librarian=mock_librarian, interval_seconds=0.01)

    task = asyncio.create_task(scheduler.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert call_count >= 2


@pytest.mark.asyncio
async def test_scheduler_records_next_run_after_each_cycle() -> None:
    """After every cycle (success or failure) the scheduler stamps next_run_at."""
    mock_librarian = AsyncMock()
    mock_librarian.consolidate = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
    scheduler = LibrarianScheduler(librarian=mock_librarian, interval_seconds=0.01)

    before = datetime.now(UTC)
    task = asyncio.create_task(scheduler.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert mock_librarian.record_next_run.await_count >= 1
    next_run = mock_librarian.record_next_run.await_args[0][0]
    assert before <= next_run <= datetime.now(UTC) + timedelta(seconds=0.01)


@pytest.mark.asyncio
async def test_scheduler_survives_record_next_run_failure() -> None:
    mock_librarian = AsyncMock()
    mock_librarian.consolidate = AsyncMock(return_value={"entries_processed": 0})
    mock_librarian.record_next_run = AsyncMock(side_effect=ConnectionError("redis down"))
    scheduler = LibrarianScheduler(librarian=mock_librarian, interval_seconds=0.01)

    task = asyncio.create_task(scheduler.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert mock_librarian.consolidate.call_count >= 2


@pytest.mark.asyncio
async def test_scheduler_stamps_next_run_at_startup() -> None:
    """A restart refreshes next_run_at before the first (slow) cycle can finish."""
    blocked = asyncio.Event()

    async def _never_finishes() -> dict[str, int]:
        await blocked.wait()
        return {"entries_processed": 0}

    mock_librarian = AsyncMock()
    mock_librarian.consolidate = AsyncMock(side_effect=_never_finishes)
    scheduler = LibrarianScheduler(librarian=mock_librarian, interval_seconds=0.01)

    task = asyncio.create_task(scheduler.run())
    await asyncio.sleep(0.05)

    mock_librarian.consolidate.assert_awaited_once()  # still mid-cycle
    assert mock_librarian.record_next_run.await_count == 1

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
