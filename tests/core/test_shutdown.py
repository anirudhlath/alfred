"""The teardown helpers: every resource gets closed, even when one of them misbehaves."""

from __future__ import annotations

import asyncio

import pytest

from core.shutdown import close_all, drain_tasks


async def test_close_all_runs_every_closer_in_order() -> None:
    order: list[str] = []

    async def _close(name: str) -> None:
        order.append(name)

    await close_all({"a": lambda: _close("a"), "b": lambda: _close("b")})
    assert order == ["a", "b"]


async def test_close_all_skips_none() -> None:
    """Callers hold optional resources; a None entry must not need a branch at the call site."""
    closed: list[str] = []

    async def _close() -> None:
        closed.append("redis")

    await close_all({"embedding provider": None, "redis": _close})
    assert closed == ["redis"]


async def test_close_all_continues_after_a_failure() -> None:
    """The whole point: a failing close must not strand the resources behind it."""
    closed: list[str] = []

    async def _boom() -> None:
        raise RuntimeError("pool already gone")

    async def _ok() -> None:
        closed.append("redis")

    await close_all({"embedding provider": _boom, "redis": _ok})
    assert closed == ["redis"]


async def test_close_all_reraises_cancellation_after_closing_everything() -> None:
    """Cancellation during teardown must not skip the rest — but must still be observed."""
    closed: list[str] = []

    async def _cancelled() -> None:
        raise asyncio.CancelledError

    async def _ok() -> None:
        closed.append("redis")

    with pytest.raises(asyncio.CancelledError):
        await close_all({"embedding provider": _cancelled, "redis": _ok})
    assert closed == ["redis"]


async def test_drain_tasks_waits_for_cancellation_to_land() -> None:
    """cancel() only requests; without the await a task can still hold the resource."""
    released = asyncio.Event()

    async def _worker() -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            released.set()
            raise

    task = asyncio.create_task(_worker())
    await asyncio.sleep(0)  # let it reach the await
    await drain_tasks(task)
    assert task.done()
    assert released.is_set()


async def test_drain_tasks_ignores_none_and_empty() -> None:
    await drain_tasks()
    await drain_tasks(None)


async def test_drain_tasks_does_not_hang_on_an_uncancellable_task() -> None:
    """A task that swallows cancellation gets logged, not allowed to wedge shutdown."""

    refusing = True

    async def _stubborn() -> None:
        while True:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                if refusing:
                    continue  # deliberately refuses to die
                raise

    task = asyncio.create_task(_stubborn())
    await asyncio.sleep(0)
    await drain_tasks(task, timeout=0.05)
    assert not task.done()

    refusing = False
    await drain_tasks(task)
    assert task.done()
