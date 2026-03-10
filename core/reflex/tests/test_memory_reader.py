"""Tests for Markdown preference reader."""

import os
import tempfile


def test_read_single_preference_file() -> None:
    from core.reflex.memory_reader import read_preferences

    with tempfile.TemporaryDirectory() as tmpdir:
        pref_file = os.path.join(tmpdir, "lighting.md")
        with open(pref_file, "w") as f:
            f.write("""---
domain: home
updated: 2026-03-10
confidence: manual
---
# Lighting Preferences

- I prefer dim lighting when watching TV or movies
- Default brightness during daytime: 80%
""")

        prefs = read_preferences(tmpdir)
        assert "lighting" in prefs.lower() or "dim" in prefs.lower()
        assert "watching TV" in prefs


def test_read_multiple_preference_files() -> None:
    from core.reflex.memory_reader import read_preferences

    with tempfile.TemporaryDirectory() as tmpdir:
        for name, content in [
            ("lighting.md", "# Lighting\n- Dim when watching TV\n"),
            ("media.md", "# Media\n- Usually watch in living room\n"),
        ]:
            with open(os.path.join(tmpdir, name), "w") as f:
                f.write(content)

        prefs = read_preferences(tmpdir)
        assert "Dim when watching TV" in prefs
        assert "living room" in prefs


def test_read_preferences_empty_directory() -> None:
    from core.reflex.memory_reader import read_preferences

    with tempfile.TemporaryDirectory() as tmpdir:
        prefs = read_preferences(tmpdir)
        assert prefs == ""
