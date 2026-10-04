"""A throwaway Redis with the query engine, so the eval can never touch a live one.

RediSearch indexes only live in DB 0, so a separate DB number on a shared server
would still share ``idx:context`` with whatever runs there. The eval therefore gets
a server of its own: by default a fresh container per policy, published on a random
loopback port and removed afterwards. ``--redis-url`` points it at one you started
yourself instead, and every connection — managed or not — must prove it is empty
before anything is written.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING

from shared.redis_streams import create_redis

if TYPE_CHECKING:
    from types import TracebackType

    from shared.types import AioRedis

logger = logging.getLogger(__name__)

# The image the fat Alfred image copies its redis-server and modules from
# (Containerfile), so the eval runs the production query engine.
DEFAULT_REDIS_IMAGE = "redis:8-bookworm"
_READY_ATTEMPTS = 50
_READY_DELAY_SECONDS = 0.1


class SandboxError(RuntimeError):
    """The eval refused a Redis it could not prove was its own."""


async def _docker(*args: str) -> str:
    process = await asyncio.create_subprocess_exec(
        "docker",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await process.communicate()
    if process.returncode != 0:
        raise SandboxError(f"docker {args[0]} failed: {err.decode().strip()}")
    return out.decode().strip()


async def assert_disposable(redis: AioRedis) -> None:
    """Refuse any server that already holds data or lacks the query engine.

    An empty keyspace is the proof that nothing here belongs to anyone else: the
    deployed stack's Redis is never empty, so pointing the eval at it fails here,
    before the first write.
    """
    modules = await redis.execute_command("MODULE", "LIST")  # type: ignore[no-untyped-call]
    names = {_module_name(module) for module in modules}
    if "search" not in names:
        raise SandboxError("this Redis has no query engine (needs Redis 8 or Redis Stack)")
    keys = int(await redis.dbsize())
    indexes = await redis.execute_command("FT._LIST")  # type: ignore[no-untyped-call]
    if keys or indexes:
        raise SandboxError(
            f"refusing a Redis that already holds {keys} keys and {len(indexes)} indexes — "
            "the memory eval only runs against an empty, disposable server"
        )


def _module_name(module: object) -> str:
    """``MODULE LIST`` entries are mappings (RESP3) or flat key/value lists (RESP2)."""
    if isinstance(module, dict):
        fields = {_text(k): v for k, v in module.items()}
    elif isinstance(module, (list, tuple)):
        items = list(module)
        fields = {_text(items[i]): items[i + 1] for i in range(0, len(items) - 1, 2)}
    else:
        return ""
    return _text(fields.get("name", ""))


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


class RedisSandbox:
    """``async with RedisSandbox(...) as redis`` — an empty Redis that is gone afterwards.

    With ``url`` the server is yours to start and stop; the sandbox still verifies it
    is empty on entry and flushes what the eval wrote on exit. ``forbidden_url`` (the
    configured ``REDIS_URL``) is refused outright, empty or not.
    """

    def __init__(
        self,
        url: str | None = None,
        *,
        image: str = DEFAULT_REDIS_IMAGE,
        forbidden_url: str | None = None,
    ) -> None:
        if url is not None and forbidden_url is not None and url == forbidden_url:
            raise SandboxError(f"{url} is the configured REDIS_URL — start a throwaway instead")
        self._url = url
        self._image = image
        self._container: str | None = None
        self._redis: AioRedis | None = None
        self.version = ""

    async def __aenter__(self) -> AioRedis:
        url = self._url or await self._start_container()
        redis = create_redis(url)
        self._redis = redis
        for attempt in range(_READY_ATTEMPTS):
            try:
                await redis.ping()
                break
            except Exception:
                if attempt == _READY_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(_READY_DELAY_SECONDS)
        await assert_disposable(redis)
        info = await redis.info("server")
        self.version = str(info.get("redis_version", ""))
        return redis

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._redis is not None:
            if self._container is None:
                # Proven empty on entry, so everything here is the eval's own.
                await self._redis.flushdb()
            await self._redis.aclose()
        if self._container is not None:
            await _docker("rm", "-f", self._container)
            self._container = None

    async def _start_container(self) -> str:
        name = f"alfred-memory-eval-{uuid.uuid4().hex[:8]}"
        await _docker("run", "-d", "--rm", "--name", name, "-p", "127.0.0.1::6379", self._image)
        self._container = name
        port_line = (await _docker("port", name, "6379/tcp")).splitlines()[0]
        host, _, port = port_line.rpartition(":")
        logger.info("Started throwaway Redis %s on %s:%s", name, host, port)
        return f"redis://{host}:{port}/0"
