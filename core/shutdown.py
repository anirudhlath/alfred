"""Orderly teardown — the counterpart to :mod:`core.warmup`.

Services release several resources when they stop, and a bare chain of ``await`` calls
in a ``finally`` block is fragile in a way that only shows up when something is already
going wrong:

* ``Task.cancel()`` merely *requests* cancellation. Without awaiting the task, it can
  still be mid-``await`` — and so still using the Redis client or the embedding
  provider — when the next line closes that resource out from under it. The visible
  symptom is a shutdown warning blaming ``EMBEDDING_HOST`` for a close we caused.
* The first close that raises skips every close after it, so the resource torn down
  last silently stops being torn down at all. A ``CancelledError`` delivered while the
  block runs does the same thing, at the first ``await``.

Hence one entry point, :func:`teardown`, rather than two composable halves. Draining and
closing have to sit inside the *same* cancellation deferral: composed at a call site the
drain becomes the first unprotected ``await`` in the ``finally``, and a cancellation
there skips every close — reintroducing the exact failure above, one line earlier.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

# A task that ignores cancellation must not be able to hang shutdown forever.
_DRAIN_TIMEOUT_SECONDS = 5.0


async def teardown(
    *,
    tasks: Sequence[asyncio.Task[Any] | None] = (),
    closers: Mapping[str, Callable[[], Awaitable[object]] | None] | None = None,
    timeout: float = _DRAIN_TIMEOUT_SECONDS,
) -> None:
    """Stop background tasks, then release resources, under one cancellation deferral.

    ``None`` entries are skipped in both arguments, so a caller holding an optional task
    or resource needs no branch at the call site.

    Tasks are drained before resources are closed because a live task may still be using
    one. Failures are logged rather than raised — teardown has nowhere useful to
    propagate to, and one failed close must not strand the resources behind it. A
    cancellation arriving at any point is held back until every phase has run, then
    re-raised so the caller still observes it.
    """
    deferred = await _drain_tasks(tasks, timeout)
    # Both phases run unconditionally — `await` on the right of `or` would short-circuit
    # away the closers whenever the drain was cancelled, which is the whole failure this
    # function exists to prevent. `or` then keeps the earlier of the two cancellations.
    closed = await _close_all(closers or {})
    cancelled = deferred or closed
    if cancelled is not None:
        raise cancelled


async def _drain_tasks(
    tasks: Sequence[asyncio.Task[Any] | None],
    timeout: float,
) -> asyncio.CancelledError | None:
    """Cancel tasks and wait for them to actually stop. Returns a deferred cancellation."""
    live = [task for task in tasks if task is not None]
    if not live:
        return None
    for task in live:
        task.cancel()
    try:
        # asyncio.wait, not wait_for(gather(...)): on timeout wait_for cancels the inner
        # future and then *awaits* it, so a task that swallows cancellation hangs the
        # very call meant to bound it. wait just stops waiting and returns what is left.
        done, pending = await asyncio.wait(live, timeout=timeout)
    except asyncio.CancelledError as exc:
        # Deferred, not propagated: the resources behind this call still need closing,
        # and this is the first await in the caller's finally block.
        logger.warning("shutdown: cancelled while draining {} task(s)", len(live))
        return exc
    for task in done:
        # Retrieve outcomes so a task that failed rather than cancelled cleanly does
        # not resurface later as "Task exception was never retrieved".
        if not task.cancelled() and task.exception() is not None:
            logger.warning("shutdown: task {} failed: {}", task.get_name(), task.exception())
    if pending:
        logger.warning(
            "shutdown: {} task(s) did not stop within {}s: {}",
            len(pending),
            timeout,
            ", ".join(sorted(task.get_name() for task in pending)),
        )
    return None


async def _close_all(
    closers: Mapping[str, Callable[[], Awaitable[object]] | None],
) -> asyncio.CancelledError | None:
    """Close every resource in order, even if an earlier one fails."""
    cancelled: asyncio.CancelledError | None = None
    for name, close in closers.items():
        if close is None:
            continue
        try:
            await close()
        except asyncio.CancelledError as exc:
            # Named, because this is the one path where a resource is skipped outright
            # — everything else either closed or logged why it could not.
            logger.warning("shutdown: cancelled while closing {} — it may still be open", name)
            cancelled = exc
        except Exception as exc:
            logger.warning("shutdown: closing {} failed ({}): {}", name, type(exc).__name__, exc)
    return cancelled
