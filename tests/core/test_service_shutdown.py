"""Every service that builds an embedding provider must close it on the way out.

The provider owns an httpx connection pool under ``EMBEDDING_BACKEND=openai``, and the
services that leak it hardest are the short-lived ones. A source-level check cannot see
this: the call has to actually be awaited on the path that runs at shutdown, so these
drive each entry point's ``finally`` with a provider that counts.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from shared.config import AlfredConfig

if TYPE_CHECKING:
    from pathlib import Path


class CountingProvider:
    """Minimal EmbeddingProvider stand-in that records its own teardown."""

    def __init__(self) -> None:
        self.closed = 0

    async def embed(self, text: str) -> list[float]:
        return [0.0, 0.0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.0, 0.0] for _ in texts]

    def dimension(self) -> int:
        return 2

    def model_name(self) -> str:
        return "counting/fake"

    async def warmup(self) -> None:
        return None

    async def aclose(self) -> None:
        self.closed += 1


class FakeRedis:
    def __init__(self) -> None:
        self.closed = 0

    async def aclose(self) -> None:
        self.closed += 1


def _sleeper(*_args: Any, **_kwargs: Any) -> asyncio.Task[None]:
    """Stand-in for start_warmup: a task that is still running at teardown."""

    async def _idle() -> None:
        await asyncio.sleep(3600)

    return asyncio.create_task(_idle())


# --------------------------------------------------------------------------
# Memory ingestor
# --------------------------------------------------------------------------


async def test_ingestor_closes_the_embedding_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    import core.memory.embedding_backend as embedding_backend
    import core.memory.ingestor_main as ingestor_main

    embedder = CountingProvider()
    redis = FakeRedis()

    async def _noop_ingestor(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(ingestor_main, "create_redis", lambda _url: redis)
    monkeypatch.setattr(embedding_backend, "build_embedding_provider", lambda _c: embedder)
    monkeypatch.setattr(ingestor_main, "start_warmup", _sleeper)
    monkeypatch.setattr(ingestor_main, "run_ingestor", _noop_ingestor)

    await ingestor_main.run(AlfredConfig())

    assert embedder.closed == 1
    assert redis.closed == 1


async def test_ingestor_closes_redis_when_the_provider_cannot_be_built(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A rejected backend used to escape with the Redis client created above still open."""
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    import core.memory.embedding_backend as embedding_backend
    import core.memory.ingestor_main as ingestor_main

    redis = FakeRedis()

    def _boom(_config: AlfredConfig) -> None:
        raise RuntimeError("Unknown EMBEDDING_BACKEND 'banana'")

    monkeypatch.setattr(ingestor_main, "create_redis", lambda _url: redis)
    monkeypatch.setattr(embedding_backend, "build_embedding_provider", _boom)
    monkeypatch.setattr(ingestor_main, "start_warmup", _sleeper)

    with pytest.raises(RuntimeError, match="Unknown EMBEDDING_BACKEND"):
        await ingestor_main.run(AlfredConfig())

    assert redis.closed == 1


# --------------------------------------------------------------------------
# Librarian
# --------------------------------------------------------------------------


async def test_librarian_closes_the_embedding_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The biggest leak: one cycle per invocation, so an open pool leaks every run."""
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    import core.librarian.__main__ as librarian_main

    embedder = CountingProvider()
    redis = FakeRedis()

    class _FakeLibrarian:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def consolidate(self) -> dict[str, int]:
            return {"consolidated": 0}

    monkeypatch.setattr(librarian_main, "create_redis", lambda _url: redis)
    monkeypatch.setattr(librarian_main, "build_embedding_provider", lambda _c: embedder)
    monkeypatch.setattr(librarian_main, "Librarian", _FakeLibrarian)

    await librarian_main.run()

    assert embedder.closed == 1
    assert redis.closed == 1


async def test_librarian_closes_redis_when_memory_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The early return for a dead memory system still has to release Redis."""
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    import core.librarian.__main__ as librarian_main

    redis = FakeRedis()

    def _boom(_config: AlfredConfig) -> None:
        raise RuntimeError("no embedding backend")

    monkeypatch.setattr(librarian_main, "create_redis", lambda _url: redis)
    monkeypatch.setattr(librarian_main, "build_embedding_provider", _boom)

    await librarian_main.run()

    assert redis.closed == 1


# --------------------------------------------------------------------------
# Conscious engine
# --------------------------------------------------------------------------


async def _drive_conscious(
    monkeypatch: pytest.MonkeyPatch,
    redis: FakeRedis,
    embedder: CountingProvider,
) -> None:
    """Run the conscious engine's startup and teardown with everything else stubbed.

    run() is a 250-line god function, so this stubs its collaborators down to the ones
    that own a resource. The main loop is ``while not _shutdown.is_set()``, so
    pre-signalling drops straight through to the teardown under test — with all seven
    background tasks live, which is the state the teardown has to survive.
    """
    import core.conscious.__main__ as conscious_main
    import core.notifications.delivery as delivery

    class _FakeTriggerStore:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def load(self) -> None:
            return None

        async def start_sync(self) -> None:
            return None

        async def stop_sync(self) -> None:
            return None

    class _FakeEngine:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        has_routine_store = False

    class _FakeScratchpadWriter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def run(self) -> None:
            await asyncio.sleep(3600)

    async def _noop(*_a: Any, **_k: Any) -> None:
        return None

    async def _idle_worker(*_a: Any, **_k: Any) -> None:
        await asyncio.sleep(3600)

    monkeypatch.setattr(conscious_main, "create_redis", lambda _url: redis)
    monkeypatch.setattr(conscious_main, "ensure_consumer_group", _noop)
    # Patched on the consumer, not the source: conscious imports the factory at module
    # scope, so the name is already bound here.
    monkeypatch.setattr(conscious_main, "build_embedding_provider", lambda _c: embedder)
    monkeypatch.setattr(conscious_main, "start_warmup", _sleeper)
    monkeypatch.setattr(conscious_main, "TriggerStore", _FakeTriggerStore)
    monkeypatch.setattr(conscious_main, "ConsciousEngine", _FakeEngine)
    monkeypatch.setattr(conscious_main, "ScratchpadWriter", _FakeScratchpadWriter)
    monkeypatch.setattr(conscious_main, "HomeAgent", lambda **_k: object())
    monkeypatch.setattr(delivery, "notification_delivery_worker", _idle_worker)

    conscious_main._shutdown.set()
    try:
        await conscious_main.run(AlfredConfig())
    finally:
        conscious_main._shutdown.clear()


async def test_conscious_closes_the_embedding_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The longest teardown: seven background tasks, any of which may hold the provider."""
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    embedder = CountingProvider()
    redis = FakeRedis()

    await _drive_conscious(monkeypatch, redis, embedder)

    assert embedder.closed == 1
    assert redis.closed == 1


async def test_conscious_closes_redis_even_when_the_provider_close_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One failing close must not strand the Redis teardown behind it."""
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))

    class _AngryProvider(CountingProvider):
        async def aclose(self) -> None:
            self.closed += 1
            raise RuntimeError("pool already gone")

    embedder = _AngryProvider()
    redis = FakeRedis()

    await _drive_conscious(monkeypatch, redis, embedder)

    assert embedder.closed == 1
    assert redis.closed == 1
