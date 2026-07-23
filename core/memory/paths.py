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


def _copy_if_missing(src_file: Path, dest_root: Path, rel_parts: tuple[str, ...]) -> None:
    target = dest_root.joinpath(*rel_parts)
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_file, target)


def seed_defaults() -> None:
    """Copy shipped read-only templates into the data dir when missing. Idempotent.

    Templates shipped under a `.example/` subdirectory (preferences, profile) are
    promoted to top-level active files — the `.example` path component is stripped
    so `MemoryReader`'s top-level-only glob actually picks them up. Routine templates
    (already top-level YAML) are copied as-is.

    Seeding runs in two ordered passes so a real (non-`.example`) package file ALWAYS
    wins over a `.example` template of the same name, deterministically — never left
    to filesystem/glob traversal order (`Path.rglob` order is unspecified):

    1. Real, non-`.example` files seed first (unchanged Part 1 behavior).
    2. `.example` templates promote to top-level, filling gaps only — the
       never-overwrite guard (shared with pass 1) means a real file seeded in pass 1
       blocks its same-named template in pass 2.
    """
    for src, dest, pattern in _seed_specs():
        if not src.is_dir():
            continue
        real_files: list[Path] = []
        example_files: list[Path] = []
        for f in src.rglob(pattern):
            if not f.is_file():
                continue
            (example_files if ".example" in f.relative_to(src).parts else real_files).append(f)

        for f in real_files:
            _copy_if_missing(f, dest, f.relative_to(src).parts)
        for f in example_files:
            rel_parts = tuple(p for p in f.relative_to(src).parts if p != ".example")
            _copy_if_missing(f, dest, rel_parts)
