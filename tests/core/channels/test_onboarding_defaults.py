"""Tests for onboarding default preference writing."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi.testclient import TestClient


def test_onboarding_writes_defaults_for_null_fields(web_client: TestClient, tmp_path: Path) -> None:
    """When all fields are null, defaults should be written."""
    import core.channels.web_server as ws

    prefs_dir = tmp_path / "preferences"
    profile_dir = tmp_path / "profile"
    prefs_dir.mkdir(parents=True)
    profile_dir.mkdir(parents=True)

    written: dict[str, str] = {}
    orig = ws._atomic_write

    def capture(path: Path, content: str) -> None:
        written[path.name] = content
        orig(path, content)

    with (
        patch.object(ws, "_atomic_write", side_effect=capture),
        patch.object(ws, "_get_prefs_dirs", return_value=(prefs_dir, profile_dir)),
    ):
        client = web_client
        resp = client.post("/api/onboarding", json={})

    assert resp.status_code == 200
    assert "personal.md" in written
    assert "07:00" in written["personal.md"]
    assert "proactivity.md" in written
    assert "moderate" in written["proactivity.md"]


def test_onboarding_does_not_overwrite_existing(web_client: TestClient, tmp_path: Path) -> None:
    """If preference files already exist, defaults should NOT overwrite them."""
    import core.channels.web_server as ws

    prefs_dir = tmp_path / "preferences"
    profile_dir = tmp_path / "profile"
    prefs_dir.mkdir(parents=True)
    profile_dir.mkdir(parents=True)

    (prefs_dir / "personal.md").write_text("existing content")
    (profile_dir / "proactivity.md").write_text("existing proactivity")

    written: dict[str, str] = {}
    orig = ws._atomic_write

    def capture(path: Path, content: str) -> None:
        written[path.name] = content
        orig(path, content)

    with (
        patch.object(ws, "_atomic_write", side_effect=capture),
        patch.object(ws, "_get_prefs_dirs", return_value=(prefs_dir, profile_dir)),
    ):
        client = web_client
        resp = client.post("/api/onboarding", json={})

    assert resp.status_code == 200
    assert "personal.md" not in written
    assert "proactivity.md" not in written
    assert (prefs_dir / "personal.md").read_text() == "existing content"
