"""Cookie-based auth middleware for FastAPI."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

if TYPE_CHECKING:
    from fastapi import Request
    from starlette.responses import Response

from shared.streams import AUTH_SESSION_PREFIX

COOKIE_NAME = "alfred_auth"


class AuthCookieMiddleware(BaseHTTPMiddleware):
    """Read alfred_auth cookie and inject authenticated state into request."""

    def __init__(self, app: Any, redis: Any = None) -> None:
        super().__init__(app)
        self._redis = redis

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.authenticated = False
        request.state.credential_id = None

        redis = self._redis or getattr(request.app.state, "redis", None)
        session_id = request.cookies.get(COOKIE_NAME)
        if session_id and redis is not None:
            try:
                data: dict[bytes, bytes] = await redis.hgetall(f"{AUTH_SESSION_PREFIX}{session_id}")
                if data and data.get(b"authenticated") == b"1":
                    request.state.authenticated = True
                    raw_cred = data.get(b"credential_id", b"")
                    request.state.credential_id = raw_cred.decode()
            except Exception:
                logger.warning("Auth session lookup failed — treating as unauthenticated")

        return await call_next(request)
