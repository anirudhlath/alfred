"""Tests for core/channels/service_credentials.py — helpers (worker tests come later)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException

from core.channels.service_credentials import (
    build_service_info,
    get_service_manifest,
    list_service_manifests,
    parse_schema,
    push_credentials,
    service_payload_healthy,
    stored_pushable_credentials,
    validate_credential_body,
)

# ── registry reads ──


@pytest.mark.asyncio
async def test_list_service_manifests_filters_and_survives_garbage(
    home_service_manifest: dict[str, Any],
) -> None:
    plain = {
        "service_name": "plain",
        "service_endpoint": "http://x/mcp",
        "features": [],
        "credentials_schema": None,
        "credentials_endpoint": None,
    }
    redis = AsyncMock()
    redis.hgetall = AsyncMock(
        return_value={
            b"home-service": json.dumps(home_service_manifest).encode(),
            b"plain": json.dumps(plain).encode(),
            b"broken": b"{not json",
        }
    )
    manifests = await list_service_manifests(redis)
    assert set(manifests) == {"home-service"}
    assert manifests["home-service"]["credentials_endpoint"] == "http://localhost:8000/credentials"


@pytest.mark.asyncio
async def test_get_service_manifest_found(home_service_manifest: dict[str, Any]) -> None:
    redis = AsyncMock()
    redis.hget = AsyncMock(return_value=json.dumps(home_service_manifest).encode())
    manifest = await get_service_manifest(redis, "home-service")
    assert manifest is not None
    assert manifest["service_name"] == "home-service"


@pytest.mark.asyncio
async def test_get_service_manifest_none_for_missing() -> None:
    redis = AsyncMock()
    redis.hget = AsyncMock(return_value=None)
    assert await get_service_manifest(redis, "nope") is None


@pytest.mark.asyncio
async def test_get_service_manifest_none_without_schema() -> None:
    plain = {"service_name": "plain", "service_endpoint": "http://x/mcp", "features": []}
    redis = AsyncMock()
    redis.hget = AsyncMock(return_value=json.dumps(plain).encode())
    assert await get_service_manifest(redis, "plain") is None


# ── validation ──


def test_validate_credential_body_rejects_unknown(home_service_manifest: dict[str, Any]) -> None:
    schema = parse_schema(home_service_manifest)
    with pytest.raises(HTTPException) as exc_info:
        validate_credential_body(schema, {"url": "http://x", "token": "t", "bogus": "v"})
    assert exc_info.value.status_code == 422


def test_validate_credential_body_rejects_missing_required(
    home_service_manifest: dict[str, Any],
) -> None:
    schema = parse_schema(home_service_manifest)
    with pytest.raises(HTTPException) as exc_info:
        validate_credential_body(schema, {"url": "http://x"})
    assert exc_info.value.status_code == 422


def test_validate_credential_body_accepts_complete(home_service_manifest: dict[str, Any]) -> None:
    schema = parse_schema(home_service_manifest)
    validate_credential_body(schema, {"url": "http://x", "token": "t"})  # no raise


# ── GET entry shape (contract C5) ──


@pytest.mark.asyncio
async def test_build_service_info_shape(home_service_manifest: dict[str, Any]) -> None:
    from shared.secrets import set_secret

    set_secret("home-service", "url", "http://192.168.50.159:8123")
    info = await build_service_info("home-service", home_service_manifest)
    assert info["name"] == "home-service"
    assert info["kind"] == "service"
    assert info["category"] == "service"
    assert info["configured"] == {"url": True, "token": False}
    assert info["schema"]["fields"]["token"]["field_type"] == "password"


# ── keyring completeness ──


@pytest.mark.asyncio
async def test_stored_pushable_credentials_requires_all_required(
    home_service_manifest: dict[str, Any],
) -> None:
    from shared.secrets import set_secret

    schema = parse_schema(home_service_manifest)
    assert await stored_pushable_credentials("home-service", schema) is None

    set_secret("home-service", "url", "http://192.168.50.159:8123")
    assert await stored_pushable_credentials("home-service", schema) is None  # token missing

    set_secret("home-service", "token", "tok")
    assert await stored_pushable_credentials("home-service", schema) == {
        "url": "http://192.168.50.159:8123",
        "token": "tok",
    }


# ── push (contract C4) ──


@pytest.mark.asyncio
async def test_push_credentials_posts_flat_json() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "ok", "health": {"status": "ok"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await push_credentials(
            http, "http://localhost:8000/credentials", {"url": "u", "token": "t"}
        )
    assert seen == [{"url": "u", "token": "t"}]


@pytest.mark.asyncio
async def test_push_credentials_raises_on_error_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await push_credentials(http, "http://localhost:8000/credentials", {"url": "u"})


# ── health convention ──


def test_service_payload_healthy() -> None:
    connected = {
        "status": "ok",
        "service": "home-service",
        "ha": {"state": "connected", "entities": 87, "areas": 6, "last_event_age_s": 2.1},
    }
    assert service_payload_healthy(200, connected) is True
    assert service_payload_healthy(503, connected) is False
    assert service_payload_healthy(200, {"status": "error"}) is False
    assert service_payload_healthy(200, {"status": "ok"}) is True  # no components → healthy
    auth_failed = {"status": "ok", "ha": {"state": "auth_failed", "entities": 0}}
    assert service_payload_healthy(200, auth_failed) is False
