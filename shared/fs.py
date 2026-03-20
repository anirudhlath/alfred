"""Filesystem utilities — shared across packages."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def atomic_write(path: Path, content: str) -> None:
    """Write content to a file atomically via tmp + rename."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content)
    os.rename(tmp, path)
