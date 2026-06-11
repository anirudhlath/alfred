"""SPA serving — static assets + index.html fallback for client-side routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI


def mount_spa(app: FastAPI, dist: Path) -> None:
    """Serve a built SPA: real files when they exist, index.html otherwise."""
    if not dist.is_dir():
        return

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        candidate = (dist / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
            return FileResponse(candidate)
        return FileResponse(dist / "index.html")
