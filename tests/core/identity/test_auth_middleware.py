"""Tests for auth cookie middleware."""

from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from core.identity.auth_middleware import AuthCookieMiddleware
from shared.streams import AUTH_SESSION_PREFIX


def _make_app(redis_mock: AsyncMock) -> FastAPI:
    """Build a minimal FastAPI app with auth middleware."""
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "authenticated": getattr(request.state, "authenticated", False),
                "credential_id": getattr(request.state, "credential_id", None),
            }
        )

    app.add_middleware(AuthCookieMiddleware, redis=redis_mock)
    return app


class TestAuthCookieMiddleware:
    def test_no_cookie_unauthenticated(self) -> None:
        redis_mock = AsyncMock()
        app = _make_app(redis_mock)
        client = TestClient(app)
        resp = client.get("/test")
        assert resp.status_code == 200
        assert resp.json()["authenticated"] is False

    def test_valid_cookie_authenticated(self) -> None:
        redis_mock = AsyncMock()
        redis_mock.hgetall.return_value = {
            b"authenticated": b"1",
            b"credential_id": b"test-cred-id",
            b"created_at": b"2026-04-16T00:00:00+00:00",
        }
        app = _make_app(redis_mock)
        client = TestClient(app)
        client.cookies.set("alfred_auth", "valid-session-id")
        resp = client.get("/test")
        assert resp.status_code == 200
        assert resp.json()["authenticated"] is True
        assert resp.json()["credential_id"] == "test-cred-id"
        redis_mock.hgetall.assert_called_once_with(f"{AUTH_SESSION_PREFIX}valid-session-id")

    def test_expired_cookie_unauthenticated(self) -> None:
        redis_mock = AsyncMock()
        redis_mock.hgetall.return_value = {}  # expired/missing session
        app = _make_app(redis_mock)
        client = TestClient(app)
        client.cookies.set("alfred_auth", "expired-session")
        resp = client.get("/test")
        assert resp.status_code == 200
        assert resp.json()["authenticated"] is False

    def test_redis_error_unauthenticated(self) -> None:
        redis_mock = AsyncMock()
        redis_mock.hgetall.side_effect = Exception("Redis down")
        app = _make_app(redis_mock)
        client = TestClient(app)
        client.cookies.set("alfred_auth", "some-session")
        resp = client.get("/test")
        assert resp.status_code == 200
        assert resp.json()["authenticated"] is False
