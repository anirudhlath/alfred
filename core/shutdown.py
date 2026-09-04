"""Orderly teardown — the counterpart to :mod:`core.warmup`.

Services release several resources in a ``finally`` block, and a bare chain of
``await`` calls there is fragile in a way that only shows up when something is
already going wrong:

* ``Task.cancel()`` merely *requests* cancellation. Without awaiting the task, it can
  still be mid-``await`` — and so still using the Redis client or the embedding
  provider — when the next line closes that resource out from under it. The visible
  symptom is a shutdown warning blaming ``EMBEDDING_HOST`` for a close we caused.
* The first close that raises skips every close after it, so the resource torn down
  last silently stops being torn down at all. A ``CancelledError`` delivered while the
  block runs does the same thing, at the first ``await``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# A task that ignores cancellation must not be able to hang shutdown forever.
_DRAIN_TIMEOUT_SECONDS = 5.0


async def drain_tasks(
    *tasks: asyncio.Task[Any] | None,
    timeout: float = _DRAIN_TIMEOUT_SECONDS,
) -> None:
    """Cancel background tasks and wait for them to actually stop.

    ``None`` entries are skipped, so callers can pass optional tasks directly. Waiting
    is bounded: a task that swallows cancellation gets logged, not allowed to wedge the
    process.
    """
    live = [task for task in tasks if task is not None]
    if not live:
        return
    for task in live:
        task.cancel()
    # asyncio.wait, not wait_for(gather(...)): on timeout wait_for cancels the inner
    # future and then *awaits* it, so a task that swallows cancellation hangs the very
    # call meant to bound it. wait just stops waiting and hands back what is pending.
    done, pending = await asyncio.wait(live, timeout=timeout)
    for task in done:
        # Retrieve outcomes so a task that failed rather than cancelled cleanly does
        # not resurface later as "Task exception was never retrieved".
        if not task.cancelled() and task.exception() is not None:
            logger.warning("shutdown: task {} failed: {}", task.get_name(), task.exception())
    if pending:
        logger.warning("shutdown: {} task(s) did not stop within {}s", len(pending), timeout)


async def close_all(closers: dict[str, Callable[[], Awaitable[object]] | None]) -> None:
    """Close every resource in order, even if an earlier one fails.

    ``None`` values are skipped so a caller holding an optional resource needs no
    branch. Failures are logged rather than raised — teardown has nowhere useful to
    propagate to, and one failed close must not strand the resources behind it. A
    cancellation is held back and re-raised once everything has had its chance to
    close, so the caller still observes it.
    """
    cancelled: asyncio.CancelledError | None = None
    for name, close in closers.items():
        if close is None:
            continue
        try:
            await close()
        except asyncio.CancelledError as exc:
            cancelled = exc
        except Exception as exc:
            logger.warning("shutdown: closing {} failed ({}): {}", name, type(exc).__name__, exc)
    if cancelled is not None:
        raise cancelled
