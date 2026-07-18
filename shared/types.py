"""Shared type aliases used across multiple packages."""

from __future__ import annotations

import redis.asyncio as aioredis

# PEP 695 type alias — the canonical location for cross-package use.
# redis 8's asyncio Redis client is no longer Generic (no type parameter).
type AioRedis = aioredis.Redis
