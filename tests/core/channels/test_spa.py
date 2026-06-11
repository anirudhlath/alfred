"""SPA fallback serving."""

from pathlib import Path

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
