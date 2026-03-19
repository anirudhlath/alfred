"""Tests for @traced decorator."""

from __future__ import annotations

import pytest

from shared.traced import traced


@traced(name="test.sync_fn")
def sync_function(x: int) -> int:
    return x * 2


@traced(name="test.async_fn")
async def async_function(x: int) -> int:
    return x * 3


def test_traced_sync() -> None:
    result = sync_function(5)
    assert result == 10


@pytest.mark.asyncio
async def test_traced_async() -> None:
    result = await async_function(5)
    assert result == 15


@traced()
def auto_named_fn() -> str:
    return "hello"


def test_traced_auto_names_from_function() -> None:
    result = auto_named_fn()
    assert result == "hello"
