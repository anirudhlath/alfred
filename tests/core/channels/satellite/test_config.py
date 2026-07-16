"""Satellite YAML config loader."""

from pathlib import Path

import pytest

from core.channels.satellite.config import SatelliteEntry, load_satellites


def test_load_satellites(tmp_path: Path) -> None:
    cfg = tmp_path / "satellites.yaml"
    cfg.write_text(
        """
satellites:
  - name: kitchen
    host: 192.168.1.40
    area: Kitchen
  - name: office
    host: office-sat.local
    port: 10701
"""
    )
    entries = load_satellites(cfg)
    assert entries == [
        SatelliteEntry(name="kitchen", host="192.168.1.40", port=10700, area="Kitchen"),
        SatelliteEntry(name="office", host="office-sat.local", port=10701, area=None),
    ]


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_satellites(tmp_path / "nope.yaml") == []


def test_env_var_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "custom.yaml"
    cfg.write_text("satellites:\n  - name: a\n    host: h\n")
    monkeypatch.setenv("SATELLITES_CONFIG", str(cfg))
    assert load_satellites()[0].name == "a"


def test_malformed_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "bad.yaml"
    cfg.write_text("satellites:\n  - host-only: true\n")
    with pytest.raises(ValueError):
        load_satellites(cfg)


def test_duplicate_names_raise(tmp_path: Path) -> None:
    cfg = tmp_path / "dup.yaml"
    cfg.write_text("satellites:\n  - name: a\n    host: h1\n  - name: a\n    host: h2\n")
    with pytest.raises(ValueError):
        load_satellites(cfg)
