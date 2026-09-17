"""Fakes shared across the test tree.

The repo-root `conftest.py` owns process-wide setup (keyring, data dir, env pinning);
this module owns the small Redis stand-ins that more than one package's tests need, so
a change to how the production pool behaves is made once rather than in four files.

Deliberately **not** a `tests/conftest.py`: pytest loads initial conftests for every
`testpaths` entry at startup, which would import this `tests` package before `sdk/tests`
(which has an `__init__.py` but no `sdk/__init__.py` above it, so pytest names its
modules `tests.*` too) and shadow it — every sdk test then fails to collect. A plain
module is imported only when a test under `tests/` asks for it, which is after `sdk`.
"""

from __future__ import annotations

import itertools
from contextlib import contextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Iterator


def aiter_values(items: Iterable[Any]) -> AsyncIterator[Any]:
    """An async iterator over ``items`` — what ``redis.scan_iter(...)`` returns.

    ``scan_iter`` is a *synchronous* method that returns an async iterator, so it is
    stubbed with a ``MagicMock`` whose return value this supplies. Items are yielded
    exactly as given: the production pool runs ``decode_responses=False`` and hands
    back bytes, and several tests deliberately mix in a ``str`` so both branches of
    ``shared.streams.decode_stream_value`` stay exercised.
    """

    async def _gen() -> AsyncIterator[Any]:
        for item in items:
            yield item

    return _gen()


def attention_redis(sets: dict[str, set[str]] | None = None) -> AsyncMock:
    """A Redis fake over the attention key space, for both sides of it.

    ``core/reflex/attention.py`` (SADD/SREM/SISMEMBER/SMEMBERS) and the admin API's
    attention routes (the same, plus the SCAN) work the same keys, so they share one
    fake rather than keeping two that can drift.

    Members are held as ``str`` in ``sets`` — passed in by a caller that wants to
    assert against it, and also reachable as ``.sets`` on the returned mock — while
    ``smembers`` and ``scan_iter`` hand back **bytes**, as the production pool does.
    Every command is a mock, so a test can override one with a failure and assert on
    the calls the others received.
    """
    store: dict[str, set[str]] = {} if sets is None else sets

    async def _sadd(key: str, member: str) -> int:
        store.setdefault(key, set()).add(member)
        return 1

    async def _srem(key: str, member: str) -> int:
        store.get(key, set()).discard(member)
        return 1

    async def _sismember(key: str, member: str) -> bool:
        return member in store.get(key, set())

    async def _smembers(key: str) -> set[bytes]:
        return {m.encode() for m in store.get(key, set())}

    def _scan_iter(match: str = "*", count: int = 100) -> AsyncIterator[Any]:
        prefix = match.rstrip("*")
        # Insertion order, like SCAN: sorting and decoding are the caller's job.
        return aiter_values([k.encode() for k in store if k.startswith(prefix)])

    r = AsyncMock()
    r.sets = store
    r.sadd = AsyncMock(side_effect=_sadd)
    r.srem = AsyncMock(side_effect=_srem)
    r.sismember = AsyncMock(side_effect=_sismember)
    r.smembers = AsyncMock(side_effect=_smembers)
    r.scan_iter = MagicMock(side_effect=_scan_iter)
    return r


@contextmanager
def pinned_probe_clock(elapsed_ms: float = 12.3) -> Iterator[float]:
    """Freeze the web channel's probe clock so ``latency_ms`` is an exact number.

    An integration status probe reads ``time.perf_counter()`` twice — once before the
    call, once inside ``_elapsed_ms`` — so a pair of readings per request pins the
    reported latency exactly, instead of the ``>= 0.0`` that any clock satisfies.

    ``core/channels/web_server.py`` uses ``time`` for nothing else, so the whole module
    reference is swapped for a stub rather than patching the stdlib clock out from
    under httpx and anyio. The readings cycle, so several probes in one test each get
    the same elapsed figure. Yields that figure.
    """
    import core.channels.web_server as ws_mod

    readings = itertools.cycle([0.0, elapsed_ms / 1000.0])
    with patch.object(ws_mod, "time", SimpleNamespace(perf_counter=lambda: next(readings))):
        yield elapsed_ms
