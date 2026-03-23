"""Tests for shared.secrets — keyring wrapper."""

from __future__ import annotations

import pytest

from shared.secrets import (
    adelete_secret,
    aget_all_secrets,
    aget_secret,
    aset_secret,
    delete_secret,
    get_all_secrets,
    get_secret,
    set_secret,
)


def test_set_and_get_secret() -> None:
    set_secret("test_integration", "username", "alice")
    assert get_secret("test_integration", "username") == "alice"


def test_get_secret_not_found() -> None:
    assert get_secret("nonexistent", "field") is None


def test_delete_secret() -> None:
    set_secret("test_integration", "password", "s3cret")
    delete_secret("test_integration", "password")
    assert get_secret("test_integration", "password") is None


def test_delete_secret_nonexistent_no_error() -> None:
    delete_secret("nonexistent", "field")


def test_get_all_secrets() -> None:
    set_secret("cal", "url", "https://caldav.example.com")
    set_secret("cal", "user", "bob")
    result = get_all_secrets("cal", ["url", "user", "password"])
    assert result == {"url": "https://caldav.example.com", "user": "bob"}
    assert "password" not in result


def test_get_all_secrets_empty() -> None:
    result = get_all_secrets("empty", ["a", "b"])
    assert result == {}


@pytest.mark.asyncio
async def test_async_set_and_get() -> None:
    await aset_secret("async_test", "key", "value")
    result = await aget_secret("async_test", "key")
    assert result == "value"


@pytest.mark.asyncio
async def test_async_delete() -> None:
    await aset_secret("async_test", "key", "value")
    await adelete_secret("async_test", "key")
    result = await aget_secret("async_test", "key")
    assert result is None


@pytest.mark.asyncio
async def test_async_get_all() -> None:
    await aset_secret("async_all", "a", "1")
    await aset_secret("async_all", "b", "2")
    result = await aget_all_secrets("async_all", ["a", "b", "c"])
    assert result == {"a": "1", "b": "2"}
