"""Tests for integration settings REST API endpoints.

Uses InMemoryKeyring from conftest.py (autouse fixture).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

from core.integrations.base import (
    CredentialField,
    CredentialSchema,
    Integration,
    IntegrationCapability,
    IntegrationRequest,
    IntegrationResult,
)
from core.integrations.registry import IntegrationRegistry


class _TestAdapter(Integration):
    name = "test_adapter"
    category = "testing"
    credentials_schema = CredentialSchema(
        fields={
            "api_url": CredentialField(label="API URL", field_type="url"),
            "token": CredentialField(label="Token", field_type="password"),
            "mfa": CredentialField(label="MFA", required=False, transient=True),
        }
    )

    def __init__(self, api_url: str = "", token: str = "", mfa: str = "") -> None:
        self.api_url = api_url
        self.token = token

    async def get_capabilities(self) -> list[IntegrationCapability]:
        return []

    async def execute(self, request: IntegrationRequest) -> IntegrationResult:
        return IntegrationResult(data={}, freshness=datetime.now(UTC), confidence=0.0)

    async def health_check(self) -> bool:
        return bool(self.api_url)


@pytest.fixture(autouse=True)
def _setup() -> None:
    """Reset registry. Keyring mock is handled by conftest."""
    IntegrationRegistry._registry.clear()
    IntegrationRegistry._instances.clear()
    IntegrationRegistry._registry["test_adapter"] = _TestAdapter


def test_list_integrations(web_client: TestClient) -> None:
    client = web_client
    resp = client.get("/api/integrations")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    adapter = next(a for a in data if a["name"] == "test_adapter")
    assert adapter["category"] == "testing"
    assert "api_url" in adapter["schema"]["fields"]
    assert "token" in adapter["schema"]["fields"]
    assert adapter["configured"]["api_url"] is False
    assert adapter["configured"]["token"] is False


def test_list_integrations_never_returns_values(web_client: TestClient) -> None:
    from shared.secrets import set_secret

    set_secret("test_adapter", "token", "super_secret_value")

    client = web_client
    resp = client.get("/api/integrations")
    body = resp.text
    assert "super_secret_value" not in body


def test_save_credentials(web_client: TestClient) -> None:
    client = web_client
    resp = client.put(
        "/api/integrations/test_adapter/credentials",
        json={"api_url": "https://api.example.com", "token": "abc123"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    from shared.secrets import get_secret

    assert get_secret("test_adapter", "api_url") == "https://api.example.com"
    assert get_secret("test_adapter", "token") == "abc123"


def test_save_credentials_transient_not_stored(web_client: TestClient) -> None:
    client = web_client
    resp = client.put(
        "/api/integrations/test_adapter/credentials",
        json={"api_url": "https://x.com", "token": "t", "mfa": "123456"},
    )
    assert resp.status_code == 200

    from shared.secrets import get_secret

    assert get_secret("test_adapter", "mfa") is None


def test_save_credentials_unknown_fields_rejected(web_client: TestClient) -> None:
    client = web_client
    resp = client.put(
        "/api/integrations/test_adapter/credentials",
        json={"api_url": "https://x.com", "token": "t", "bogus": "value"},
    )
    assert resp.status_code == 422


def test_save_credentials_missing_required_rejected(web_client: TestClient) -> None:
    client = web_client
    resp = client.put(
        "/api/integrations/test_adapter/credentials",
        json={"api_url": "https://x.com"},
    )
    assert resp.status_code == 422


def test_save_credentials_unknown_integration(web_client: TestClient) -> None:
    client = web_client
    resp = client.put(
        "/api/integrations/nonexistent/credentials",
        json={"key": "value"},
    )
    assert resp.status_code == 404


def test_delete_credentials(web_client: TestClient) -> None:
    from shared.secrets import set_secret

    set_secret("test_adapter", "api_url", "https://old.com")
    set_secret("test_adapter", "token", "old_token")

    client = web_client
    resp = client.delete("/api/integrations/test_adapter/credentials")
    assert resp.status_code == 200

    from shared.secrets import get_secret

    assert get_secret("test_adapter", "api_url") is None
    assert get_secret("test_adapter", "token") is None


def test_health_check_endpoint(web_client: TestClient) -> None:
    from shared.secrets import set_secret

    set_secret("test_adapter", "api_url", "https://api.example.com")
    set_secret("test_adapter", "token", "t")

    IntegrationRegistry.reconfigure("test_adapter")

    client = web_client
    resp = client.get("/api/integrations/test_adapter/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "test_adapter"
    assert data["healthy"] is True
