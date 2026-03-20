"""Filesystem utilities — shared across packages."""

from __future__ import annotations

import os
import tempfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def atomic_write(path: Path, content: str) -> None:
    """Write content to a file atomically via tmp + rename.

    Uses a unique tempfile in the same directory to avoid races when
    multiple writers target the same file concurrently.
    """
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        os.unlink(tmp_name)
        raise
