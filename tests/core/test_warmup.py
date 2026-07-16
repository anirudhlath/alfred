"""Tests for the background startup warmup helper."""

from __future__ import annotations

import asyncio

import pytest

from core.warmup import start_warmup


@pytest.mark.asyncio
async def test_runs_all_steps() -> None:
    ran: list[str] = []

    async def step_a() -> None:
        ran.append("a")

    async def step_b() -> None:
        ran.append("b")

    task = start_warmup("test", {"step a": step_a, "step b": step_b})
    await asyncio.wait_for(task, timeout=2.0)

    assert sorted(ran) == ["a", "b"]


@pytest.mark.asyncio
async def test_steps_run_concurrently() -> None:
    """Steps must not run sequentially — a slow model load must not delay others."""
    barrier = asyncio.Barrier(2)

    async def step() -> None:
        # Only passes if both steps are in flight at the same time.
        await asyncio.wait_for(barrier.wait(), timeout=2.0)

    task = start_warmup("test", {"one": step, "two": step})
    await asyncio.wait_for(task, timeout=3.0)


@pytest.mark.asyncio
async def test_failing_step_is_non_fatal() -> None:
    ran: list[str] = []

    async def bad_step() -> None:
        raise RuntimeError("model exploded")

    async def good_step() -> None:
        ran.append("good")

    task = start_warmup("test", {"bad": bad_step, "good": good_step})
    await asyncio.wait_for(task, timeout=2.0)  # must not raise

    assert ran == ["good"]


@pytest.mark.asyncio
async def test_warmup_task_is_cancellable() -> None:
    started = asyncio.Event()

    async def hanging_step() -> None:
        started.set()
        await asyncio.sleep(60)

    task = start_warmup("test", {"hang": hanging_step})
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
