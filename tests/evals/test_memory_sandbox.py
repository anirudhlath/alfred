"""The eval refuses any Redis it cannot prove is disposable."""

from __future__ import annotations

import pytest

from evals.memory.sandbox import RedisSandbox, SandboxError, assert_disposable
from tests.evals.memory_fakes import FakeRedis


async def test_an_empty_server_with_the_query_engine_passes() -> None:
    await assert_disposable(FakeRedis())  # type: ignore[arg-type]


async def test_a_server_holding_any_key_is_refused() -> None:
    with pytest.raises(SandboxError, match="already holds 1 keys"):
        await assert_disposable(FakeRedis(extra_keys=1))  # type: ignore[arg-type]


async def test_a_server_holding_an_index_is_refused() -> None:
    with pytest.raises(SandboxError, match="1 indexes"):
        await assert_disposable(FakeRedis(indexes=("idx:context",)))  # type: ignore[arg-type]


async def test_a_server_without_the_query_engine_is_refused() -> None:
    with pytest.raises(SandboxError, match="no query engine"):
        await assert_disposable(FakeRedis(modules=("ReJSON",)))  # type: ignore[arg-type]


def test_the_configured_redis_is_refused_before_connecting() -> None:
    with pytest.raises(SandboxError, match="configured REDIS_URL"):
        RedisSandbox("redis://localhost:6379/0", forbidden_url="redis://localhost:6379/0")
