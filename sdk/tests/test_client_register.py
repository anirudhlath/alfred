"""Tests for AlfredClient credential declaration + ServiceRegistered publication."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from sdk.alfred_sdk.client import AlfredClient
from sdk.alfred_sdk.events import ServiceRegistered
from sdk.alfred_sdk.feature import CredentialField, CredentialSchema

HA_SCHEMA = CredentialSchema(
    fields={
        "url": CredentialField(
            label="Home Assistant URL",
            field_type="url",
            default="http://homeassistant.local:8123",
        ),
        "token": CredentialField(label="Access Token", field_type="password"),
    }
)


def _mock_redis() -> AsyncMock:
    mock = AsyncMock()
    mock.hset = AsyncMock()
    mock.xadd = AsyncMock()
    mock.set = AsyncMock()
    mock.aclose = AsyncMock()
    return mock


def test_client_stores_credential_config() -> None:
    client = AlfredClient(
        service_name="home-service",
        credentials_schema=HA_SCHEMA,
        credentials_endpoint="http://localhost:8000/credentials",
    )
    assert client.credentials_schema is HA_SCHEMA
    assert client.credentials_endpoint == "http://localhost:8000/credentials"


def test_client_credential_config_defaults_none() -> None:
    client = AlfredClient(service_name="plain-service")
    assert client.credentials_schema is None
    assert client.credentials_endpoint is None


def test_manifest_includes_credentials() -> None:
    client = AlfredClient(
        service_name="home-service",
        service_endpoint="http://localhost:8000/mcp",
        credentials_schema=HA_SCHEMA,
        credentials_endpoint="http://localhost:8000/credentials",
    )
    manifest = client.get_registration_manifest()
    assert manifest["service_name"] == "home-service"
    assert manifest["credentials_endpoint"] == "http://localhost:8000/credentials"
    assert manifest["credentials_schema"]["fields"]["token"]["field_type"] == "password"


def test_manifest_defaults_to_no_credentials() -> None:
    client = AlfredClient(service_name="plain-service")
    manifest = client.get_registration_manifest()
    assert manifest["credentials_schema"] is None
    assert manifest["credentials_endpoint"] is None


@pytest.mark.asyncio
async def test_register_publishes_service_registered_after_hset() -> None:
    mock_redis = _mock_redis()
    call_order: list[str] = []
    mock_redis.hset.side_effect = lambda *a, **k: call_order.append("hset")
    mock_redis.xadd.side_effect = lambda *a, **k: call_order.append("xadd")

    client = AlfredClient(
        service_name="home-service",
        credentials_schema=HA_SCHEMA,
        credentials_endpoint="http://localhost:8000/credentials",
    )
    with patch("redis.asyncio.from_url", return_value=mock_redis):
        await client.register()

    assert call_order == ["hset", "xadd"]

    args: tuple[Any, ...] = mock_redis.xadd.call_args[0]
    stream, payload = args
    assert stream == "alfred:events"
    event = ServiceRegistered.model_validate_json(payload["event"])
    assert event.source == "home-service"
    assert event.service_name == "home-service"
    assert event.credentials_endpoint == "http://localhost:8000/credentials"
    assert event.has_credentials_schema is True


@pytest.mark.asyncio
async def test_register_publishes_even_without_credentials() -> None:
    mock_redis = _mock_redis()
    client = AlfredClient(service_name="plain-service")
    with patch("redis.asyncio.from_url", return_value=mock_redis):
        await client.register()

    payload = mock_redis.xadd.call_args[0][1]
    event = ServiceRegistered.model_validate_json(payload["event"])
    assert event.service_name == "plain-service"
    assert event.has_credentials_schema is False
    assert event.credentials_endpoint is None
