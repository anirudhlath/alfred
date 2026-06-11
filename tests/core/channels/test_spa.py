"""SPA fallback serving."""

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.channels.spa import mount_spa


def _dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>alfred</html>")
    (dist / "assets" / "app.js").write_text("// js")
    return dist


def test_serves_static_files(tmp_path: Path) -> None:
    app = FastAPI()
    mount_spa(app, _dist(tmp_path))
    client = TestClient(app)
    assert client.get("/assets/app.js").status_code == 200
    assert "alfred" in client.get("/").text


def test_client_routes_fall_back_to_index(tmp_path: Path) -> None:
    app = FastAPI()
    mount_spa(app, _dist(tmp_path))
    client = TestClient(app)
    resp = client.get("/activity")
    assert resp.status_code == 200
    assert "alfred" in resp.text


def test_missing_dist_is_noop(tmp_path: Path) -> None:
    app = FastAPI()
    mount_spa(app, tmp_path / "nope")
    client = TestClient(app)
    assert client.get("/").status_code == 404


# ---------------------------------------------------------------------------
# Path traversal / escape tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "/%2e%2e/secret",
        "/%2e%2e%2f%2e%2e%2fetc%2fpasswd",
        "/../secret",
        "/../../etc/passwd",
    ],
)
def test_path_traversal_falls_back_to_index(tmp_path: Path, url: str) -> None:
    """Percent-encoded and plain traversal sequences must never escape dist."""
    dist = _dist(tmp_path)
    # Place a sentinel file one level above dist so traversal would find it.
    secret = tmp_path / "secret"
    secret.write_text("SECRET CONTENT")

    app = FastAPI()
    mount_spa(app, dist)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get(url)
    # Must serve index.html (200) and must NOT expose secret content.
    assert resp.status_code == 200
    assert "alfred" in resp.text
    assert "SECRET" not in resp.text


def test_symlink_escape_falls_back_to_index(tmp_path: Path) -> None:
    """A symlink inside dist pointing outside must not be served."""
    dist = _dist(tmp_path)
    # Create a sensitive file outside dist.
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE CONTENT")
    # Symlink it into dist.
    link = dist / "escaped.txt"
    os.symlink(outside, link)

    app = FastAPI()
    mount_spa(app, dist)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/escaped.txt")
    # Resolved path is outside dist — must fall back to index.html.
    assert resp.status_code == 200
    assert "alfred" in resp.text
    assert "OUTSIDE" not in resp.text
