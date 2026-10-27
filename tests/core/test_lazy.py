"""core.lazy.Lazy: build once, off the loop, from any loop (issue #97)."""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.lazy import Lazy


class _Build:
    """A slow build with no guard of its own, so building once is Lazy's doing alone."""

    def __init__(self, delay: float = 0.05) -> None:
        self.value = object()
        self.calls = 0
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self._delay = delay

    def __call__(self) -> object:
        self.calls += 1
        self.started.set()
        time.sleep(self._delay)  # a model load, long enough for callers to overlap
        self.release.wait(timeout=5)
        return self.value


async def test_concurrent_first_callers_build_once() -> None:
    build = _Build()
    lazy = Lazy(build)

    results = await asyncio.gather(*(lazy.aget() for _ in range(4)))

    assert results == [build.value] * 4
    assert build.calls == 1


def test_concurrent_threads_build_once() -> None:
    build = _Build()
    lazy = Lazy(build)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = [f.result(timeout=5) for f in [pool.submit(lazy.get) for _ in range(4)]]

    assert results == [build.value] * 4
    assert build.calls == 1


async def test_build_runs_off_the_event_loop() -> None:
    loop_thread = threading.get_ident()
    build_threads: list[int] = []

    def build() -> int:
        build_threads.append(threading.get_ident())
        return 1

    assert await Lazy(build).aget() == 1
    assert build_threads and build_threads[0] != loop_thread


async def test_built_value_is_answered_without_leaving_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lazy = Lazy(_Build(delay=0))
    first = await lazy.aget()

    def _no_thread(*args: object, **kwargs: object) -> object:
        raise AssertionError("a built value must not hop to a worker thread")

    monkeypatch.setattr(asyncio, "to_thread", _no_thread)

    assert await lazy.aget() is first


def test_callers_on_different_event_loops_all_work() -> None:
    """The lock must not bind to the first loop that contends on it.

    Each ``asyncio.run`` is a fresh loop. Two first callers contend on each one; an
    asyncio.Lock would raise "bound to a different event loop" on the second.
    """
    build = _Build()
    lazy = Lazy(build)

    async def _two_first_calls() -> list[object]:
        return list(await asyncio.gather(lazy.aget(), lazy.aget()))

    for _ in range(2):
        lazy.reset()
        assert asyncio.run(_two_first_calls()) == [build.value, build.value]

    assert build.calls == 2  # one per loop: each round started unbuilt


async def test_cancelled_caller_does_not_start_a_second_build() -> None:
    """Cancelling a caller cannot stop its worker thread, so its build runs on. The
    lock must stay held for as long as that build does, or the next caller starts a
    second build beside it."""
    build = _Build(delay=0)
    build.release.clear()
    lazy = Lazy(build)

    first = asyncio.create_task(lazy.aget())
    assert await asyncio.to_thread(build.started.wait, 5)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    second = asyncio.create_task(lazy.aget())
    await asyncio.sleep(0.1)  # time enough for a second build to begin, were it free to
    build.release.set()

    assert await second is build.value
    assert build.calls == 1


async def test_failed_build_is_not_cached() -> None:
    calls = 0

    def build() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("download failed")
        return "model"

    lazy = Lazy(build)

    with pytest.raises(OSError, match="download failed"):
        await lazy.aget()
    assert await lazy.aget() == "model"
    assert await lazy.aget() == "model"
    assert calls == 2


async def test_none_is_cached_like_any_value() -> None:
    """Call sites cache a permanent failure as None, so None must count as built."""
    calls = 0

    def build() -> None:
        nonlocal calls
        calls += 1

    lazy: Lazy[None] = Lazy(build)

    assert await lazy.aget() is None
    assert lazy.get() is None
    assert calls == 1


async def test_shared_lock_makes_builds_take_turns() -> None:
    active = 0
    most_active = 0
    counter_lock = threading.Lock()

    def build() -> str:
        nonlocal active, most_active
        with counter_lock:
            active += 1
            most_active = max(most_active, active)
        time.sleep(0.05)  # long enough for two unserialised builds to overlap
        with counter_lock:
            active -= 1
        return "built"

    shared = threading.Lock()
    first, second = Lazy(build, lock=shared), Lazy(build, lock=shared)

    assert await asyncio.gather(first.aget(), second.aget()) == ["built", "built"]
    assert most_active == 1
