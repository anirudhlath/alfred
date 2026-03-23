"""Tests for IntegrationRegistry keyring auto-population.

Uses InMemoryKeyring from tests/conftest.py (autouse fixture).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.integrations.base import (
    CredentialField,
    CredentialSchema,
    Integration,
    IntegrationCapability,
    IntegrationRequest,
    IntegrationResult,
)
from core.integrations.registry import IntegrationRegistry


class _CredsAdapter(Integration):
    """Test adapter that captures credentials."""

    name = "creds_test"
    category = "test"
    credentials_schema = CredentialSchema(
        fields={
            "username": CredentialField(label="User"),
            "password": CredentialField(label="Pass", field_type="password"),
        }
    )

    def __init__(self, username: str = "", password: str = "") -> None:
        self.username = username
        self.password = password

    async def get_capabilities(self) -> list[IntegrationCapability]:
        return []

    async def execute(self, request: IntegrationRequest) -> IntegrationResult:
        return IntegrationResult(data={}, freshness=datetime.now(UTC), confidence=0.0)

    async def health_check(self) -> bool:
        return bool(self.username)


class _NoCredsAdapter(Integration):
    """Test adapter with no credentials_schema."""

    name = "no_creds"
    category = "test"

    async def get_capabilities(self) -> list[IntegrationCapability]:
        return []

    async def execute(self, request: IntegrationRequest) -> IntegrationResult:
        return IntegrationResult(data={}, freshness=datetime.now(UTC), confidence=0.0)

    async def health_check(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    """Reset registry before each test. Keyring mock is handled by conftest."""
    IntegrationRegistry._registry.clear()
    IntegrationRegistry._instances.clear()
    IntegrationRegistry._registry["creds_test"] = _CredsAdapter
    IntegrationRegistry._registry["no_creds"] = _NoCredsAdapter


def test_get_auto_populates_from_keyring() -> None:
    from shared.secrets import set_secret

    set_secret("creds_test", "username", "alice")
    set_secret("creds_test", "password", "s3cret")

    adapter = IntegrationRegistry.get("creds_test")
    assert isinstance(adapter, _CredsAdapter)
    assert adapter.username == "alice"
    assert adapter.password == "s3cret"


def test_get_explicit_kwargs_override_keyring() -> None:
    from shared.secrets import set_secret

    set_secret("creds_test", "username", "keyring_user")

    adapter = IntegrationRegistry.get(
        "creds_test", username="explicit_user", password="explicit_pass"
    )
    assert isinstance(adapter, _CredsAdapter)
    assert adapter.username == "explicit_user"
    assert adapter.password == "explicit_pass"


def test_get_empty_keyring_degrades_gracefully() -> None:
    adapter = IntegrationRegistry.get("creds_test")
    assert isinstance(adapter, _CredsAdapter)
    assert adapter.username == ""
    assert adapter.password == ""


def test_get_no_schema_adapter_unaffected() -> None:
    adapter = IntegrationRegistry.get("no_creds")
    assert isinstance(adapter, _NoCredsAdapter)


def test_reconfigure_drops_cache_and_rebuilds() -> None:
    from shared.secrets import set_secret

    adapter1 = IntegrationRegistry.get("creds_test")
    assert isinstance(adapter1, _CredsAdapter)
    assert adapter1.username == ""

    set_secret("creds_test", "username", "bob")
    set_secret("creds_test", "password", "new_pass")

    IntegrationRegistry.reconfigure("creds_test")

    adapter2 = IntegrationRegistry.get("creds_test")
    assert isinstance(adapter2, _CredsAdapter)
    assert adapter2.username == "bob"
    assert adapter2.password == "new_pass"
    assert adapter2 is not adapter1


def test_reconfigure_not_instantiated_no_error() -> None:
    IntegrationRegistry.reconfigure("no_creds")


def test_reconfigure_unregistered_no_error() -> None:
    IntegrationRegistry.reconfigure("truly_nonexistent")
