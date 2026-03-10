"""Read Markdown preference files from core/memory/preferences/.

Returns concatenated plain text for injection into the SLM prompt.
Strips YAML frontmatter — the SLM only needs the natural language content.
"""

from __future__ import annotations

import os
import re


def read_preferences(preferences_dir: str) -> str:
    """Read all .md files in the preferences directory and return concatenated content."""
    if not os.path.isdir(preferences_dir):
        return ""

    sections: list[str] = []
    for filename in sorted(os.listdir(preferences_dir)):
        if not filename.endswith(".md"):
            continue
        filepath = os.path.join(preferences_dir, filename)
        with open(filepath) as f:
            content = f.read()

        # Strip YAML frontmatter
        content = re.sub(r"^---\n.*?\n---\n", "", content, flags=re.DOTALL)
        content = content.strip()
        if content:
            sections.append(content)

    return "\n\n".join(sections)
