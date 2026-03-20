"""Shared type aliases used across multiple packages."""

from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis

# PEP 695 type alias — the canonical location for cross-package use.
type AioRedis = aioredis.Redis[Any]
