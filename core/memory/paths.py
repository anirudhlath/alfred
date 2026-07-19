"""Centralized runtime-state paths for the memory subsystem.

All writable memory state resolves through ``shared.config.data_path`` so it can be
externalized (persistent), thrown away (ephemeral), or seeded (dev). Package-shipped
preference/profile/routine files are read-only templates copied into the data dir on
first boot only.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from shared.config import data_path

_PKG_MEMORY = Path(__file__).resolve().parent  # core/memory

# Shipped read-only template dirs.
PKG_PREFERENCES = _PKG_MEMORY / "preferences"
PKG_PROFILE = _PKG_MEMORY / "profile"
PKG_ROUTINES = _PKG_MEMORY / "routines"


def scratchpad_path() -> Path:
    return data_path("scratchpad.md")


def episodic_cold_path() -> Path:
    return data_path("episodic_cold.db")


def _ensured_dir(name: str) -> Path:
    p = data_path(name)
    p.mkdir(parents=True, exist_ok=True)
    return p


def routines_dir() -> Path:
    return _ensured_dir("routines")


def preferences_dir() -> Path:
    return _ensured_dir("preferences")


def profile_dir() -> Path:
    return _ensured_dir("profile")


def triggers_snapshot_dir() -> Path:
    return _ensured_dir("triggers")


# (template src, data-dir dest factory, glob) — only content files, never package .py.
def _seed_specs() -> list[tuple[Path, Path, str]]:
    return [
        (PKG_PREFERENCES, preferences_dir(), "*.md"),
        (PKG_PROFILE, profile_dir(), "*.md"),
        (PKG_ROUTINES, routines_dir(), "*.yaml"),
    ]


def seed_defaults() -> None:
    """Copy shipped read-only templates into the data dir when missing. Idempotent."""
    for src, dest, pattern in _seed_specs():
        if not src.is_dir():
            continue
        for f in src.rglob(pattern):
            if not f.is_file():
                continue
            target = dest / f.relative_to(src)
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
