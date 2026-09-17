"""The package ships NO memory content — a production instance starts with empty memory.

Fabricated preferences/profile/routines committed to the repo are seeded into the live
data dir by ``seed_defaults()`` and then read as if the user had stated them: the Reflex
Engine feeds preferences straight into the SLM prompt, so a fixture becomes real
behaviour in a real house. Only code may ship under these package dirs.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_CONTENT_DIRS = ("preferences", "profile", "routines")
_CONTENT_SUFFIXES = {".md", ".yaml", ".yml"}


def test_package_ships_no_memory_content() -> None:
    offenders: list[str] = []
    for name in _CONTENT_DIRS:
        root = _REPO / "core" / "memory" / name
        if not root.is_dir():
            continue
        offenders.extend(
            str(f.relative_to(_REPO))
            for f in root.rglob("*")
            if f.is_file() and f.suffix in _CONTENT_SUFFIXES
        )
    assert not offenders, f"seed content committed to the package: {sorted(offenders)}"
