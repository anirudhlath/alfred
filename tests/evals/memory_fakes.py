"""Small stand-ins for the memory-eval tests: a hash-only Redis and a hashing embedder."""

from __future__ import annotations

import fnmatch
import hashlib
import math
from typing import TYPE_CHECKING, Any

from core.memory.embedding_provider import EmbeddingProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class FakeRedis:
    """Hashes, SCAN, pipelined HMGET and the handful of server commands the sandbox reads."""

    def __init__(
        self,
        hashes: dict[str, dict[str, Any]] | None = None,
        *,
        modules: tuple[str, ...] = ("search", "ReJSON"),
        indexes: tuple[str, ...] = (),
        extra_keys: int = 0,
    ) -> None:
        self.hashes: dict[str, dict[str, bytes]] = {
            key: {field: _bytes(value) for field, value in fields.items()}
            for key, fields in (hashes or {}).items()
        }
        self._modules = modules
        self._indexes = indexes
        self._extra_keys = extra_keys

    async def scan_iter(self, match: str = "*", count: int = 10) -> AsyncIterator[bytes]:
        for key in list(self.hashes):
            if fnmatch.fnmatchcase(key, match):
                yield key.encode()

    def pipeline(self, transaction: bool = True) -> _FakePipeline:
        return _FakePipeline(self)

    async def hget(self, key: str, field: str) -> bytes | None:
        return self.hashes.get(key, {}).get(field)

    async def hmget(self, key: str, fields: list[str]) -> list[bytes | None]:
        stored = self.hashes.get(key, {})
        return [stored.get(field) for field in fields]

    async def hset(self, key: str, mapping: dict[str, Any]) -> int:
        self.hashes.setdefault(key, {}).update({k: _bytes(v) for k, v in mapping.items()})
        return len(mapping)

    async def exists(self, key: str) -> int:
        return int(key in self.hashes)

    async def dbsize(self) -> int:
        return len(self.hashes) + self._extra_keys

    async def execute_command(self, *args: str) -> Any:
        match args:
            case ("MODULE", "LIST"):
                return [{b"name": name.encode(), b"ver": 80213} for name in self._modules]
            case ("FT._LIST",):
                return [name.encode() for name in self._indexes]
        raise NotImplementedError(args)


class _FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._queued: list[tuple[str, list[str]]] = []

    def hmget(self, key: str, fields: list[str]) -> None:
        self._queued.append((key, fields))

    async def execute(self) -> list[list[bytes | None]]:
        return [await self._redis.hmget(key, fields) for key, fields in self._queued]


def _bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    return str(value).encode()


class HashEmbedder(EmbeddingProvider):
    """Deterministic, model-free vectors: the same text always maps to the same vector."""

    def __init__(self, dim: int = 16) -> None:
        self._dim = dim
        self.calls: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        digest = hashlib.sha256(text.encode()).digest()
        raw = [(digest[i % len(digest)] - 127.5) for i in range(self._dim)]
        norm = math.sqrt(sum(x * x for x in raw))
        return [x / norm for x in raw]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(text) for text in texts]

    def dimension(self) -> int:
        return self._dim

    def model_name(self) -> str:
        return "hash-embedder"
