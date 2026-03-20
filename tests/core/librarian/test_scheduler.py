"""Tests for Librarian periodic scheduling."""

from __future__ import annotations

import asyncio
import contextlib
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
