"""SPA serving — static assets + index.html fallback for client-side routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI
    from starlette.middleware.base import RequestResponseEndpoint
    from starlette.requests import Request
    from starlette.responses import Response

_IMMUTABLE = "public, max-age=31536000, immutable"
_NO_STORE = "no-cache, no-store, must-revalidate"

# Prefixes that must 404 (not fall back to index.html) so REST/WS clients — including
# the iOS AlfredKit client hitting a renamed endpoint — get a real 404 rather than a
# 200 HTML page they would try to parse as JSON.
_NON_SPA_PREFIXES = ("api/", "ws")
_NON_SPA_PATHS = frozenset({"health"})


def mount_spa(app: FastAPI, dist: Path) -> None:
    """Serve a built SPA: real files when they exist, index.html otherwise."""
    if not dist.is_dir():
        logger.warning(
            "SPA not mounted — {} missing. Run 'cd web && npm run build', "
            "then restart the channels process; '/' will 404 until then.",
            dist,
        )
        return

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        if full_path.startswith(_NON_SPA_PREFIXES) or full_path in _NON_SPA_PATHS:
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = (dist / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
            return FileResponse(candidate)
        return FileResponse(dist / "index.html")


class SpaCacheMiddleware(BaseHTTPMiddleware):
    """Cache policy for the SPA. Vite content-hashes everything under ``/assets/``,
    so those responses are immutable for a year; every other non-API path
    (``/``, ``/index.html``, SPA-fallback routes, the service worker, the manifest)
    is the un-hashed entry surface and must be revalidated on every load so a deploy
    takes effect immediately. ``/api/*`` is left untouched.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response: Response = await call_next(request)
        path = request.url.path
        if path.startswith("/assets/"):
            response.headers["Cache-Control"] = _IMMUTABLE
        elif not path.startswith("/api/"):
            response.headers["Cache-Control"] = _NO_STORE
        return response
