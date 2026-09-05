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


_IMMUTABLE = "public, max-age=31536000, immutable"
_NO_STORE = "no-cache, no-store, must-revalidate"
_NO_CACHE = "no-cache, must-revalidate"


class SpaCacheMiddleware(BaseHTTPMiddleware):
    """Stamp the SPA's cache policy on every response outside ``/api/``.

    Three tiers, and ``/api/*`` is the only thing left untouched — ``/health`` and a
    plain HTTP GET to ``/ws`` are stamped too:

    * ``/assets/*`` that did not error — Vite content-hashes these filenames, so the
      bytes behind a URL never change and they are immutable for a year. A 304 keeps
      that; a 4xx/5xx does not, because pinning a 404 for a year leaves no URL to bust.
    * ``text/html`` — the entry point and every SPA-fallback route (``/``,
      ``/index.html``, ``/activity``). Not stored at all, so a deploy is picked up on
      the next load.
    * everything else — unhashed ``web/public/`` files such as ``/favicon.svg`` and
      ``/manifest.json``. Revalidated on every load, but a 304 saves re-sending bytes
      that a deploy usually leaves unchanged.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response: Response = await call_next(request)
        path = request.url.path
        if path.startswith("/api/"):
            return response
        if path.startswith("/assets/") and response.status_code < 400:
            response.headers["Cache-Control"] = _IMMUTABLE
        elif response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = _NO_STORE
        else:
            response.headers["Cache-Control"] = _NO_CACHE
        return response
