"""Satellite fleet configuration — YAML single source of truth."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from loguru import logger
from pydantic import BaseModel, ValidationError

_DEFAULT_PATH = Path("config") / "satellites.yaml"


class SatelliteEntry(BaseModel):
    """One physical satellite device."""

    name: str
    host: str
    port: int = 10700
    area: str | None = None


def load_satellites(path: Path | None = None) -> list[SatelliteEntry]:
    """Load satellite entries from YAML. Missing file → empty fleet.

    Path resolution: explicit arg > SATELLITES_CONFIG env > config/satellites.yaml.
    """
    resolved = path or Path(os.getenv("SATELLITES_CONFIG", str(_DEFAULT_PATH)))
    if not resolved.exists():
        logger.info("No satellite config at {} — satellite bridge disabled", resolved)
        return []
    raw = yaml.safe_load(resolved.read_text()) or {}
    try:
        entries = [SatelliteEntry.model_validate(item) for item in raw.get("satellites", [])]
    except ValidationError as exc:
        raise ValueError(f"Invalid satellite config {resolved}: {exc}") from exc
    names = [e.name for e in entries]
    if len(names) != len(set(names)):
        raise ValueError(f"Duplicate satellite names in {resolved}")
    return entries
