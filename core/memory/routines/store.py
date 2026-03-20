"""RoutineStore — YAML-based procedural memory for learned routines."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

import yaml

from core.memory.schemas import RoutineSpec
from shared.fs import atomic_write

logger = logging.getLogger(__name__)

_DEFAULT_ROUTINES_DIR = str(Path(__file__).resolve().parent)


class RoutineStore:
    """Stores learned routines as YAML files on disk.

    Each routine is a separate YAML file named by the routine's name.
    Atomic writes: write to .tmp then os.rename().

    An in-memory cache is maintained after the first ``list_all()`` call.
    Any write or delete operation invalidates the cache so the next read
    re-scans the directory.
    """

    def __init__(self, routines_dir: str = _DEFAULT_ROUTINES_DIR) -> None:
        self._dir = Path(routines_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._cache: list[RoutineSpec] | None = None

    def _path(self, name: str) -> Path:
        safe_name = name.replace(" ", "_").replace("/", "_")
        return self._dir / f"{safe_name}.yaml"

    def _invalidate(self) -> None:
        """Invalidate the in-memory cache."""
        self._cache = None

    def save(self, routine: RoutineSpec) -> None:
        """Save a routine to disk (atomic write) and invalidate cache."""
        path = self._path(routine.name)
        data = routine.model_dump(mode="json")
        atomic_write(path, yaml.dump(data, default_flow_style=False, sort_keys=False))
        logger.debug("Saved routine '%s'", routine.name)
        self._invalidate()

    def get(self, name: str) -> RoutineSpec | None:
        """Load a routine by name."""
        path = self._path(name)
        if not path.exists():
            return None
        data = yaml.safe_load(path.read_text())
        return RoutineSpec.model_validate(data)

    def list_all(self) -> list[RoutineSpec]:
        """List all routines, using the in-memory cache when available."""
        if self._cache is not None:
            return list(self._cache)
        routines: list[RoutineSpec] = []
        for path in self._dir.glob("*.yaml"):
            try:
                data = yaml.safe_load(path.read_text())
                routines.append(RoutineSpec.model_validate(data))
            except Exception as e:
                logger.warning("Failed to load routine from %s: %s", path, e)
        self._cache = routines
        return list(self._cache)

    def list_by_state(
        self, state: Literal["candidate", "active", "dormant", "archived"]
    ) -> list[RoutineSpec]:
        """List routines in a specific state."""
        return [r for r in self.list_all() if r.state == state]

    def delete(self, name: str) -> None:
        """Delete a routine by name and invalidate cache."""
        path = self._path(name)
        if path.exists():
            path.unlink()
            logger.debug("Deleted routine '%s'", name)
        self._invalidate()
