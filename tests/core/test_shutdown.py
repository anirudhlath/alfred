"""Teardown must release everything it was given, including when it is itself cancelled."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from core.shutdown import teardown

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


@pytest.fixture
def captured_logs() -> Iterator[list[str]]:
    """Collect loguru output; the warnings here are the operator's only breadcrumb."""
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    yield messages
    logger.remove(sink_id)


class Stubborn:
    """A running task that swallows cancellation — until the test lets it go.

    The release valve is the point: a task that refuses forever would make its own
    cleanup an infinite loop and hang the suite instead of the shutdown.
    """

    def __init__(self) -> None:
        self.refusing = True
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> asyncio.Task[None]:
        async def _run() -> None:
            while True:
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    if self.refusing:
                        continue
                    raise

        self.task = asyncio.create_task(_run(), name="stubborn")
        await asyncio.sleep(0)  # let it reach the await
        return self.task

    async def kill(self) -> None:
        if self.task is None:
            return
        self.refusing = False
        while not self.task.done():
            self.task.cancel()
            await asyncio.sleep(0)


@pytest.fixture
async def stubborn_worker() -> AsyncIterator[Stubborn]:
    """Release the worker even when an assertion above it fails.

    Killing it as a test's last statement is not enough: a failed assertion skips the
    kill, the task then refuses cancellation forever, and the suite hangs at teardown
    instead of reporting the failure red.
    """
    worker = Stubborn()
    try:
        yield worker
    finally:
        await worker.kill()


async def test_teardown_runs_closers_in_order() -> None:
    order: list[str] = []

    async def _close(name: str) -> None:
        order.append(name)

    await teardown(closers={"a": lambda: _close("a"), "b": lambda: _close("b")})
    assert order == ["a", "b"]


async def test_teardown_skips_none_entries() -> None:
    """Callers hold optional tasks and resources; neither should need a branch."""
    closed: list[str] = []

    async def _close() -> None:
        closed.append("redis")

    await teardown(tasks=[None], closers={"embedding provider": None, "redis": _close})
    assert closed == ["redis"]


async def test_teardown_continues_after_a_failing_close(captured_logs: list[str]) -> None:
    """A failing close must not strand the resources behind it, and must name itself."""
    closed: list[str] = []

    async def _boom() -> None:
        raise RuntimeError("pool already gone")

    async def _ok() -> None:
        closed.append("redis")

    await teardown(closers={"embedding provider": _boom, "redis": _ok})

    assert closed == ["redis"]
    assert any(
        "embedding provider" in line and "pool already gone" in line for line in captured_logs
    )


async def test_teardown_reraises_a_cancellation_raised_by_a_closer(
    captured_logs: list[str],
) -> None:
    """The one path where a resource is skipped outright — so it has to be named."""
    closed: list[str] = []

    async def _cancelled() -> None:
        raise asyncio.CancelledError

    async def _ok() -> None:
        closed.append("redis")

    with pytest.raises(asyncio.CancelledError):
        await teardown(closers={"embedding provider": _cancelled, "redis": _ok})

    assert closed == ["redis"]
    assert any("cancelled while closing embedding provider" in line for line in captured_logs)


async def test_teardown_closes_everything_when_cancelled_during_the_drain(
    stubborn_worker: Stubborn,
) -> None:
    """The drain is the first await in every caller's finally, so it must defer too.

    Composed as drain-then-close at a call site, a cancellation here propagated out and
    no closer ever ran: neither the embedding provider nor Redis was released.
    """
    stubborn = await stubborn_worker.start()
    closed: list[str] = []

    async def _close(name: str) -> None:
        closed.append(name)

    running = asyncio.create_task(
        teardown(
            tasks=[stubborn],
            closers={
                "embedding provider": lambda: _close("embedding provider"),
                "redis": lambda: _close("redis"),
            },
            timeout=30,
        )
    )
    await asyncio.sleep(0.05)  # let it reach the drain's await
    running.cancel()

    with pytest.raises(asyncio.CancelledError):
        await running
    assert closed == ["embedding provider", "redis"]


async def test_teardown_waits_for_cancellation_to_land() -> None:
    """cancel() only requests; without the await a task can still hold the resource."""
    released = asyncio.Event()
    closed: list[str] = []

    async def _worker() -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            released.set()
            raise

    async def _close() -> None:
        # The assertion that matters: by the time a resource is closed, every task
        # that could still be using it has already stopped.
        assert released.is_set()
        closed.append("redis")

    task = asyncio.create_task(_worker())
    await asyncio.sleep(0)

    await teardown(tasks=[task], closers={"redis": _close})

    assert task.done()
    assert closed == ["redis"]


async def test_teardown_does_not_hang_on_an_uncancellable_task(
    captured_logs: list[str],
    stubborn_worker: Stubborn,
) -> None:
    """A task that swallows cancellation is named and left behind, never waited on forever."""
    stubborn = await stubborn_worker.start()
    closed: list[str] = []

    async def _close() -> None:
        closed.append("redis")

    await teardown(tasks=[stubborn], closers={"redis": _close}, timeout=0.05)

    assert not stubborn.done()
    # Named, not merely counted: an operator debugging a wedged shutdown needs to know
    # *which* task refused to stop.
    assert any("stubborn" in line for line in captured_logs)
    # And the resources still got released.
    assert closed == ["redis"]


async def test_teardown_reports_a_task_that_failed_rather_than_cancelled(
    captured_logs: list[str],
) -> None:
    async def _explodes() -> None:
        raise RuntimeError("worker blew up")

    task = asyncio.create_task(_explodes(), name="exploder")
    await asyncio.sleep(0)

    await teardown(tasks=[task])

    assert any("exploder" in line and "worker blew up" in line for line in captured_logs)
