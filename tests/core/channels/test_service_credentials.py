"""Tests for core/channels/service_credentials.py — helpers + ServiceRegistered worker."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import HTTPException

from bus.schemas.events import ServiceRegistered, TriggerFired
from core.channels.service_credentials import (
    build_service_info,
    credential_push_worker,
    get_service_manifest,
    list_service_manifests,
    parse_schema,
    push_credentials,
    service_payload_healthy,
    stored_pushable_credentials,
    validate_credential_body,
)
from core.channels.web_server import create_app
from shared.streams import EVENTS_STREAM

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
            b"int-value": b"123",
            b"list-value": b"[1, 2]",
        }
    )
    manifests = await list_service_manifests(redis)
    assert set(manifests) == {"home-service"}
    assert manifests["home-service"]["credentials_endpoint"] == "http://localhost:8000/credentials"


@pytest.mark.asyncio
async def test_get_service_manifest_none_for_non_object_json() -> None:
    redis = AsyncMock()
    redis.hget = AsyncMock(return_value=b"123")
    assert await get_service_manifest(redis, "int-value") is None

    redis.hget = AsyncMock(return_value=b"[1, 2]")
    assert await get_service_manifest(redis, "list-value") is None


@pytest.mark.asyncio
async def test_get_service_manifest_none_for_malformed_credentials_schema() -> None:
    manifest = {
        "service_name": "bad-schema",
        "service_endpoint": "http://x/mcp",
        "features": [],
        "credentials_schema": {"fields": "nope"},
        "credentials_endpoint": "http://x/credentials",
    }
    redis = AsyncMock()
    redis.hget = AsyncMock(return_value=json.dumps(manifest).encode())
    assert await get_service_manifest(redis, "bad-schema") is None


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


# ── SDK↔core round trip (seam) ──


@pytest.mark.asyncio
async def test_sdk_manifest_round_trips_through_core_parsing() -> None:
    """Build a manifest with a *real* AlfredClient (not the hand-rolled
    home_service_manifest fixture) and feed it through the exact core parsing
    path (get_service_manifest → _parse_manifest → build_service_info).

    The SDK never imports core (and vice versa) — the wire contract is JSON,
    so fixtures on each side can silently drift from what the other side
    actually produces/consumes. This test makes that drift impossible by
    exercising both ends for real.
    """
    from sdk.alfred_sdk import AlfredClient, CredentialField, CredentialSchema

    schema = CredentialSchema(
        fields={
            "url": CredentialField(label="Home Assistant URL", field_type="url"),
            "token": CredentialField(label="Access Token", field_type="password"),
        }
    )
    client = AlfredClient(
        service_name="home-service",
        service_endpoint="http://localhost:8000/mcp",
        credentials_schema=schema,
        credentials_endpoint="http://localhost:8000/credentials",
    )
    manifest_json = json.dumps(client.get_registration_manifest()).encode()

    redis = AsyncMock()
    redis.hget = AsyncMock(return_value=manifest_json)
    manifest = await get_service_manifest(redis, "home-service")
    assert manifest is not None
    assert manifest["credentials_endpoint"] == "http://localhost:8000/credentials"

    info = await build_service_info("home-service", manifest)
    assert set(info["schema"]["fields"]) == {"url", "token"}
    assert info["schema"]["fields"]["url"]["field_type"] == "url"
    assert info["schema"]["fields"]["token"]["field_type"] == "password"


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


# ── credential_push_worker (ServiceRegistered consumer) ──


def _stream_entries(event_json: str) -> list[Any]:
    return [(EVENTS_STREAM.encode(), [(b"1-0", {b"event": event_json.encode()})])]


def _worker_redis(
    manifest: dict[str, Any] | None, entries: list[Any]
) -> tuple[AsyncMock, asyncio.Event]:
    """AsyncMock redis whose xreadgroup yields one batch, then stops the worker."""
    shutdown = asyncio.Event()
    redis = AsyncMock()
    redis.hget = AsyncMock(
        return_value=json.dumps(manifest).encode() if manifest is not None else None
    )
    redis.xgroup_create = AsyncMock()
    redis.xack = AsyncMock()

    async def fake_xreadgroup(*args: Any, **kwargs: Any) -> list[Any]:
        shutdown.set()
        return entries

    redis.xreadgroup = AsyncMock(side_effect=fake_xreadgroup)
    return redis, shutdown


def _service_registered_json() -> str:
    return ServiceRegistered(
        source="home-service",
        service_name="home-service",
        credentials_endpoint="http://localhost:8000/credentials",
        has_credentials_schema=True,
    ).model_dump_json()


@pytest.mark.asyncio
async def test_worker_re_pushes_stored_credentials(
    home_service_manifest: dict[str, Any],
) -> None:
    from shared.secrets import set_secret

    set_secret("home-service", "url", "http://192.168.50.159:8123")
    set_secret("home-service", "token", "tok")

    redis, shutdown = _worker_redis(
        home_service_manifest, _stream_entries(_service_registered_json())
    )

    pushes: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        pushes.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "ok", "health": {"status": "ok"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await credential_push_worker(redis, http, shutdown=shutdown)

    assert pushes == [{"url": "http://192.168.50.159:8123", "token": "tok"}]
    redis.xack.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_skips_when_credentials_incomplete(
    home_service_manifest: dict[str, Any],
) -> None:
    from shared.secrets import set_secret

    set_secret("home-service", "url", "http://192.168.50.159:8123")  # token missing

    redis, shutdown = _worker_redis(
        home_service_manifest, _stream_entries(_service_registered_json())
    )

    pushes: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        pushes.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await credential_push_worker(redis, http, shutdown=shutdown)

    assert pushes == []
    redis.xack.assert_awaited_once()  # still acked — nothing to retry until user saves


@pytest.mark.asyncio
async def test_worker_ignores_other_event_types(home_service_manifest: dict[str, Any]) -> None:
    fired = TriggerFired(trigger_id="t1", trigger_name="test", trigger_type="time")
    redis, shutdown = _worker_redis(home_service_manifest, _stream_entries(fired.model_dump_json()))

    pushes: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        pushes.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await credential_push_worker(redis, http, shutdown=shutdown)

    assert pushes == []
    redis.xack.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_acks_non_object_event_json_without_crashing(
    home_service_manifest: dict[str, Any],
) -> None:
    """A stream entry whose "event" field decodes to valid-but-non-object JSON
    (e.g. a bare int) must not crash the batch — it's skipped and acked."""
    redis, shutdown = _worker_redis(home_service_manifest, _stream_entries("123"))

    pushes: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        pushes.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await credential_push_worker(redis, http, shutdown=shutdown)  # must not raise

    assert pushes == []
    redis.xack.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_push_failure_logged_and_acked(
    home_service_manifest: dict[str, Any],
) -> None:
    from shared.secrets import set_secret

    set_secret("home-service", "url", "http://192.168.50.159:8123")
    set_secret("home-service", "token", "tok")

    redis, shutdown = _worker_redis(
        home_service_manifest, _stream_entries(_service_registered_json())
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await credential_push_worker(redis, http, shutdown=shutdown)  # must not raise

    redis.xack.assert_awaited_once()


def test_lifespan_starts_credential_push_worker() -> None:
    """The channels lifespan starts the ServiceRegistered consumer (same wiring
    as the notification delivery worker). Patch set mirrors
    tests/core/channels/test_web_server.py::test_auth_status_not_shadowed_by_spa_catch_all.
    """
    from fastapi.testclient import TestClient

    calls: list[tuple[Any, Any]] = []

    async def fake_worker(
        redis: Any,
        http: Any,
        consumer: str = "worker-1",
        shutdown: asyncio.Event | None = None,
    ) -> None:
        calls.append((redis, http))

    mock_redis = AsyncMock()
    mock_redis.hgetall = AsyncMock(return_value={})
    mock_redis.close = AsyncMock()

    mock_store = AsyncMock()
    mock_store.initialize = AsyncMock()
    mock_store.close = AsyncMock()
    mock_store.get_user_id = AsyncMock(return_value=None)
    mock_store.list_credentials = AsyncMock(return_value=[])
    mock_store.has_any_credential = AsyncMock(return_value=False)

    def fake_warmup(service: str, steps: Any) -> Any:
        # The real warmup starts Whisper/Piper loads in to_thread; those
        # threads outlive the TestClient and pollute web_server._lazy_cache
        # for later tests (this file runs before test_voice_async.py).
        return asyncio.create_task(asyncio.sleep(0))

    with (
        patch("core.channels.web_server.aioredis.from_url", return_value=mock_redis),
        patch("core.channels.web_server.CredentialStore", return_value=mock_store),
        patch("core.channels.web_server._init_apns_adapter", new=AsyncMock()),
        patch("core.channels.web_server.start_warmup", new=fake_warmup),
        patch(
            "core.notifications.delivery.notification_delivery_worker",
            new=AsyncMock(return_value=None),
        ),
        patch("core.channels.service_credentials.credential_push_worker", new=fake_worker),
        patch("httpx.AsyncClient.aclose", new=AsyncMock()),
    ):
        app = create_app(redis_url="redis://localhost:6379")
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200

    assert len(calls) == 1
    assert calls[0][0] is mock_redis  # worker gets the shared pool
    assert calls[0][1] is app.state.http  # worker gets the app's long-lived httpx client
