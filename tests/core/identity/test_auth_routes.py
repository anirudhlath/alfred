"""Tests for WebAuthn auth routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from core.identity.auth_routes import create_auth_router
from core.identity.credentials import CredentialStore
from shared.streams import AUTH_SESSION_PREFIX


@pytest.fixture
async def store(tmp_path: object) -> CredentialStore:
    import pathlib

    db_path = pathlib.Path(str(tmp_path)) / "credentials.db"
    s = CredentialStore(db_path)
    await s.initialize()
    return s


@pytest.fixture
def redis_mock() -> AsyncMock:
    mock = AsyncMock()
    mock.set = AsyncMock()
    mock.get = AsyncMock(return_value=None)
    mock.delete = AsyncMock()
    mock.hset = AsyncMock()
    mock.expire = AsyncMock()
    mock.hgetall = AsyncMock(return_value={})
    return mock


@pytest.fixture
def app(store: CredentialStore, redis_mock: AsyncMock) -> FastAPI:
    app = FastAPI()
    router = create_auth_router(store=store, redis=redis_mock)
    app.include_router(router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


class TestAuthStatus:
    def test_no_credentials_registered(self, client: TestClient) -> None:
        resp = client.get("/api/auth/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["registered"] is False
        assert data["authenticated"] is False

    @pytest.mark.asyncio
    async def test_credential_registered_not_authenticated(
        self, store: CredentialStore, client: TestClient
    ) -> None:
        await store.save_credential(
            credential_id="test-cred",
            public_key=b"\x01",
            sign_count=0,
            device_name="Test",
            transports=["internal"],
        )
        resp = client.get("/api/auth/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["registered"] is True
        assert data["authenticated"] is False


def _reject_network() -> None:
    """Dependency override that always rejects as untrusted."""
    raise HTTPException(status_code=403, detail="Access restricted to trusted networks")


class TestRegistrationBegin:
    def test_returns_options(self, client: TestClient, redis_mock: AsyncMock) -> None:
        with (
            patch("core.identity.auth_routes.generate_registration_options") as mock_gen,
            patch("core.identity.auth_routes.options_to_json") as mock_json,
        ):
            mock_options = MagicMock()
            mock_options.challenge = b"\x01\x02\x03"
            mock_gen.return_value = mock_options
            mock_json.return_value = '{"test": "options"}'

            resp = client.post(
                "/api/auth/register/begin",
                json={"device_name": "MacBook Pro"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["test"] == "options"
            mock_gen.assert_called_once()

    def test_rejects_untrusted_network(self, app: FastAPI) -> None:
        from core.channels.web_server import require_trusted_network

        app.dependency_overrides[require_trusted_network] = _reject_network
        try:
            untrusted_client = TestClient(app)
            resp = untrusted_client.post(
                "/api/auth/register/begin",
                json={"device_name": "Test"},
            )
            assert resp.status_code == 403
        finally:
            app.dependency_overrides.pop(require_trusted_network, None)


class TestRegistrationComplete:
    def test_rejects_untrusted_network(self, app: FastAPI, client: TestClient) -> None:
        from core.channels.web_server import require_trusted_network

        app.dependency_overrides[require_trusted_network] = _reject_network
        try:
            resp = client.post(
                "/api/auth/register/complete",
                json={"credential": "{}"},
            )
            assert resp.status_code == 403
        finally:
            app.dependency_overrides.pop(require_trusted_network, None)


class TestLoginBegin:
    @pytest.mark.asyncio
    async def test_returns_options_with_credentials(
        self, store: CredentialStore, client: TestClient, redis_mock: AsyncMock
    ) -> None:
        await store.save_credential(
            credential_id="test-cred",
            public_key=b"\x01",
            sign_count=0,
            device_name="Test",
            transports=["internal"],
        )
        with (
            patch("core.identity.auth_routes.generate_authentication_options") as mock_gen,
            patch("core.identity.auth_routes.options_to_json") as mock_json,
        ):
            mock_options = MagicMock()
            mock_options.challenge = b"\x04\x05\x06"
            mock_gen.return_value = mock_options
            mock_json.return_value = '{"test": "auth_options"}'

            resp = client.post("/api/auth/login/begin")
            assert resp.status_code == 200
            data = resp.json()
            assert data["test"] == "auth_options"

    def test_returns_404_no_credentials(self, client: TestClient) -> None:
        resp = client.post("/api/auth/login/begin")
        assert resp.status_code == 404


class TestLogout:
    def test_clears_session_and_cookie(self, client: TestClient, redis_mock: AsyncMock) -> None:
        redis_mock.hgetall.return_value = {
            b"authenticated": b"1",
            b"credential_id": b"test",
            b"created_at": b"2026-04-16T00:00:00",
        }
        client.cookies.set("alfred_auth", "session-123")
        resp = client.post("/api/auth/logout")
        assert resp.status_code == 200
        redis_mock.delete.assert_called_once_with(f"{AUTH_SESSION_PREFIX}session-123")
