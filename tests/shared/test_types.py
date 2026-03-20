"""Verify AioRedis is importable from shared.types."""

from __future__ import annotations


def test_aioredis_importable() -> None:
    from shared.types import AioRedis

    assert AioRedis is not None
