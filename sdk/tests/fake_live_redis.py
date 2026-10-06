"""In-memory stand-in for the Redis commands live_state uses (a helper, not a conftest).

Values come back as bytes, as from a real client without decode_responses, so the
reader's decoding is exercised.
"""

from __future__ import annotations

import asyncio
from typing import Any

# Bytes as from a default client, or str as from one built with decode_responses=True.
RedisText = bytes | str


class FakePipeline:
    def __init__(self, redis: FakeLiveRedis, transaction: bool) -> None:
        self._redis = redis
        self.transaction = transaction
        self._ops: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def __aenter__(self) -> FakePipeline:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def delete(self, *keys: str) -> FakePipeline:
        self._ops.append(("delete", keys, {}))
        return self

    def hset(self, key: str, mapping: dict[str, str]) -> FakePipeline:
        self._ops.append(("hset", (key,), {"mapping": mapping}))
        return self

    def hgetall(self, key: str) -> FakePipeline:
        self._ops.append(("hgetall", (key,), {}))
        return self

    async def execute(self) -> list[Any]:
        self._redis.executes.append((self.transaction, [op for op, _, _ in self._ops]))
        return [await getattr(self._redis, op)(*args, **kw) for op, args, kw in self._ops]


class FakeLiveRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[RedisText, RedisText]] = {}
        # (transaction?, [command names]) per pipeline execute
        self.executes: list[tuple[bool, list[str]]] = []
        # When set, the next HSET sleeps this long first (write-order tests).
        self.slow_next_hset = 0.0
        self.closed = False

    async def hset(
        self,
        key: str,
        field: str | None = None,
        value: str | None = None,
        mapping: dict[str, str] | None = None,
    ) -> int:
        if self.slow_next_hset:
            delay, self.slow_next_hset = self.slow_next_hset, 0.0
            await asyncio.sleep(delay)
        items = dict(mapping or {})
        if field is not None:
            items[field] = str(value)
        target = self.hashes.setdefault(key, {})
        for f, v in items.items():
            target[f.encode()] = str(v).encode()
        return len(items)

    async def hdel(self, key: str, *fields: str) -> int:
        target = self.hashes.get(key, {})
        removed = sum(target.pop(f.encode(), None) is not None for f in fields)
        if key in self.hashes and not target:
            del self.hashes[key]
        return removed

    async def delete(self, *keys: str) -> int:
        return sum(self.hashes.pop(k, None) is not None for k in keys)

    async def hgetall(self, key: str) -> dict[RedisText, RedisText]:
        return dict(self.hashes.get(key, {}))

    async def hkeys(self, key: str) -> list[RedisText]:
        return list(self.hashes.get(key, {}))

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        return FakePipeline(self, transaction)

    async def aclose(self) -> None:
        self.closed = True
