"""MemoryReader — reads semantic memory files into structured text for the system prompt."""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


class MemoryReader:
    """Reads preference and profile Markdown files from disk.

    Provides text sections for ContextAssembler. Files are read-only at runtime
    (only the Librarian or humans edit them).
    """

    def __init__(
        self,
        preferences_dir: Path,
        profile_dir: Path,
        default_proactivity: str = "opinionated",
    ) -> None:
        self._preferences_dir = Path(preferences_dir)
        self._profile_dir = Path(profile_dir)
        self._default_proactivity = default_proactivity
        self._cached_proactivity: str | None = None

    @staticmethod
    def _read_md_body(path: Path) -> str:
        """Read a Markdown file, stripping YAML frontmatter."""
        text = path.read_text()
        # Strip YAML frontmatter (between --- markers)
        stripped = re.sub(r"^---\n.*?\n---\n*", "", text, count=1, flags=re.DOTALL)
        return stripped.strip()

    def _read_all_md(self, directory: Path) -> str:
        """Read and concatenate all .md files in a directory."""
        if not directory.is_dir():
            return ""
        parts: list[str] = []
        for path in sorted(directory.glob("*.md")):
            body = self._read_md_body(path)
            if body:
                parts.append(body)
                # Cache proactivity level when we encounter it during profile read
                if path.name == "proactivity.md" and directory == self._profile_dir:
                    match = re.search(r"Level:\s*(\w+)", body)
                    if match:
                        self._cached_proactivity = match.group(1).lower()
        return "\n\n".join(parts)

    def get_preferences(self) -> str:
        """Read all preference files and concatenate their content."""
        return self._read_all_md(self._preferences_dir)

    def get_profile(self) -> str:
        """Read all profile files and concatenate their content."""
        return self._read_all_md(self._profile_dir)

    def get_proactivity_level(self) -> str:
        """Return proactivity level, using cached value from get_profile() if available."""
        if self._cached_proactivity is not None:
            return self._cached_proactivity
        # Fallback: read directly if get_profile() wasn't called first
        proactivity_path = self._profile_dir / "proactivity.md"
        if proactivity_path.exists():
            body = self._read_md_body(proactivity_path)
            match = re.search(r"Level:\s*(\w+)", body)
            if match:
                return match.group(1).lower()
        return self._default_proactivity
