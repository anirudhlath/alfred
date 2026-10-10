"""Build a value at most once, off the event loop, safely from any event loop.

Model construction (Whisper, TTS, ECAPA, sentence-transformers) takes seconds to tens
of seconds, so it must run off the event loop, and a startup warmup racing the first
request must not load the same model twice. :class:`Lazy` is the one implementation of
both. Its lock is a ``threading.Lock`` taken *inside* the worker thread, around the
blocking build itself, never an ``asyncio.Lock`` around ``await asyncio.to_thread(...)``
(issue #97):

- an ``asyncio.Lock`` binds to the first event loop that contends on it and raises
  "bound to a different event loop" from any other, so one owned by anything that
  outlives a loop (a module, a process-wide singleton) breaks every loop after the
  first: each test's loop, each real-lifespan ``TestClient``;
- a cancelled caller releases an ``asyncio.Lock`` at once, while its worker thread
  (``asyncio.to_thread`` cannot be cancelled) goes on building, so the next caller
  starts a second build beside it. Held in the thread, the lock is held for exactly
  as long as the build runs.

A caller that queues behind a build waits in a worker thread, not on the loop. Once the
value is built, :meth:`Lazy.aget` answers without leaving the loop.
"""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


class Lazy[T]:
    """A value built at most once by the blocking ``build``.

    A build that raises caches nothing: the exception reaches the caller and the next
    access builds again. To make a failure stick, have ``build`` return a value that
    says so (``None``, say), which is cached like any other.

    ``build`` runs under the lock, so it must not reach back into this ``Lazy``, or
    into another that shares its lock: the lock is not reentrant, and the build would
    deadlock waiting for itself. Pass one ``lock`` to several instances to make their
    builds take turns.
    """

    def __init__(self, build: Callable[[], T], *, lock: threading.Lock | None = None) -> None:
        self._build = build
        self._lock = lock if lock is not None else threading.Lock()
        # A one-tuple once built, so a value of None is told apart from "not built".
        self._value: tuple[T] | None = None

    def get(self) -> T:
        """The value, built first if need be. Blocks: call it from a worker thread only."""
        value = self._value
        if value is None:
            with self._lock:
                # Re-check: the build this thread queued behind may have finished it.
                value = self._value
                if value is None:
                    value = (self._build(),)
                    self._value = value
        return value[0]

    async def aget(self) -> T:
        """The value, built in a worker thread if need be."""
        value = self._value
        if value is not None:
            return value[0]
        return await asyncio.to_thread(self.get)

    def reset(self) -> None:
        """Forget the value, so the next access builds it again (test isolation)."""
        with self._lock:
            self._value = None
