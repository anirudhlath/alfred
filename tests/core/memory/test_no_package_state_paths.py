"""No process entry point may construct package-relative writable-state paths."""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
# Matches `.parent.parent / "memory"` — the entry-point pattern reaching into the
# installed package for writable state. Read-only assets use `.parent / "episodic"`,
# which this pattern does not match.
_PATTERN = re.compile(r"""\.parent\.parent\s*/\s*["']memory["']""")


def test_no_package_relative_memory_state_paths() -> None:
    offenders: list[str] = []
    for f in (_REPO / "core").rglob("*.py"):
        s = str(f)
        if "/tests/" in s or f.name == "paths.py":
            continue
        if _PATTERN.search(f.read_text(encoding="utf-8")):
            offenders.append(str(f.relative_to(_REPO)))
    assert not offenders, f"package-relative memory state paths remain: {offenders}"
