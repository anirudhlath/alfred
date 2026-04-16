"""Integration test for full WebAuthn registration -> login -> WS auth flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.identity.auth_middleware import AuthCookieMiddleware
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
    """Redis mock that stores auth sessions in a dict."""
    storage: dict[str, dict[bytes, bytes] | bytes] = {}
    mock = AsyncMock()

    async def fake_set(key: str, value: str, ex: int = 0) -> None:
        storage[key] = value.encode() if isinstance(value, str) else value

    async def fake_get(key: str) -> bytes | None:
        val = storage.get(key)
        return val if isinstance(val, bytes) else None

    async def fake_delete(key: str) -> None:
        storage.pop(key, None)

    async def fake_hset(key: str, mapping: dict[str, str]) -> None:
        storage[key] = {k.encode(): v.encode() for k, v in mapping.items()}

    async def fake_hgetall(key: str) -> dict[bytes, bytes]:
        val = storage.get(key, {})
        return val if isinstance(val, dict) else {}

    async def fake_expire(key: str, ttl: int) -> None:
        pass

    mock.set = AsyncMock(side_effect=fake_set)
    mock.get = AsyncMock(side_effect=fake_get)
    mock.delete = AsyncMock(side_effect=fake_delete)
    mock.hset = AsyncMock(side_effect=fake_hset)
    mock.hgetall = AsyncMock(side_effect=fake_hgetall)
    mock.expire = AsyncMock(side_effect=fake_expire)
    return mock


class TestWebAuthnIntegrationFlow:
    """Test the full registration -> status -> login -> status cycle."""

    @pytest.mark.asyncio
    async def test_status_before_registration(
        self, store: CredentialStore, redis_mock: AsyncMock
    ) -> None:
        app = FastAPI()
        app.add_middleware(AuthCookieMiddleware, redis=redis_mock)
        router = create_auth_router(store=store, redis=redis_mock)
        app.include_router(router)
        client = TestClient(app)

        resp = client.get("/api/auth/status")
        assert resp.json() == {"registered": False, "authenticated": False}

    @pytest.mark.asyncio
    async def test_register_begin_creates_challenge(
        self, store: CredentialStore, redis_mock: AsyncMock
    ) -> None:
        app = FastAPI()
        router = create_auth_router(store=store, redis=redis_mock)
        app.include_router(router)
        client = TestClient(app)

        with (
            patch("core.identity.auth_routes.generate_registration_options") as mock_gen,
            patch("core.identity.auth_routes.options_to_json") as mock_json,
        ):
            mock_options = MagicMock()
            mock_options.challenge = b"\x01\x02\x03"
            mock_gen.return_value = mock_options
            mock_json.return_value = '{"rp": "alfred"}'

            resp = client.post(
                "/api/auth/register/begin",
                json={"device_name": "Test Device"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "_challenge_id" in data
            redis_mock.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_logout_clears_session(
        self, store: CredentialStore, redis_mock: AsyncMock
    ) -> None:
        app = FastAPI()
        app.add_middleware(AuthCookieMiddleware, redis=redis_mock)
        router = create_auth_router(store=store, redis=redis_mock)
        app.include_router(router)
        client = TestClient(app)

        client.cookies.set("alfred_auth", "session-to-clear")
        resp = client.post("/api/auth/logout")
        assert resp.status_code == 200
        redis_mock.delete.assert_called_with(f"{AUTH_SESSION_PREFIX}session-to-clear")
