"""Tests for MemoryReader — reads semantic memory files into text."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from core.memory.reader import MemoryReader


@pytest.fixture()
def memory_dirs(tmp_path: Path) -> tuple[Path, Path]:
    prefs = tmp_path / "preferences"
    profile = tmp_path / "profile"
    prefs.mkdir()
    profile.mkdir()
    return prefs, profile


def test_get_preferences_reads_markdown_files(
    memory_dirs: tuple[Path, Path],
) -> None:
    prefs, profile = memory_dirs
    (prefs / "personal.md").write_text(
        "---\ndomain: general\nupdated: 2026-03-19\n"
        "confidence: manual\n---\n\n# Personal\n\n- Wake time: 07:30\n"
    )
    reader = MemoryReader(preferences_dir=prefs, profile_dir=profile)
    result = reader.get_preferences()
    assert "Wake time: 07:30" in result


def test_get_preferences_empty_dir(memory_dirs: tuple[Path, Path]) -> None:
    prefs, profile = memory_dirs
    reader = MemoryReader(preferences_dir=prefs, profile_dir=profile)
    assert reader.get_preferences() == ""


def test_get_profile_reads_markdown_files(
    memory_dirs: tuple[Path, Path],
) -> None:
    prefs, profile = memory_dirs
    (profile / "about.md").write_text(
        "---\ntype: semantic\n---\n\n# About Sir\n\n- Enjoys classical music\n"
    )
    reader = MemoryReader(preferences_dir=prefs, profile_dir=profile)
    result = reader.get_profile()
    assert "Enjoys classical music" in result


def test_get_proactivity_level_from_profile(
    memory_dirs: tuple[Path, Path],
) -> None:
    prefs, profile = memory_dirs
    (profile / "proactivity.md").write_text(
        "---\ndomain: general\nupdated: 2026-03-19\n"
        "confidence: manual\n---\n\n# Proactivity Level\n\n- Level: moderate\n"
    )
    reader = MemoryReader(preferences_dir=prefs, profile_dir=profile)
    assert reader.get_proactivity_level() == "moderate"


def test_get_proactivity_level_default(
    memory_dirs: tuple[Path, Path],
) -> None:
    prefs, profile = memory_dirs
    reader = MemoryReader(
        preferences_dir=prefs, profile_dir=profile, default_proactivity="conservative"
    )
    assert reader.get_proactivity_level() == "conservative"


def test_multiple_preference_files_concatenated(
    memory_dirs: tuple[Path, Path],
) -> None:
    prefs, profile = memory_dirs
    (prefs / "personal.md").write_text("---\n---\n\n# Personal\n\n- Wake: 07:30\n")
    (prefs / "routines.md").write_text("---\n---\n\n# Routines\n\n- Morning: lights 80%\n")
    reader = MemoryReader(preferences_dir=prefs, profile_dir=profile)
    result = reader.get_preferences()
    assert "Wake: 07:30" in result
    assert "Morning: lights 80%" in result


def test_preferences_cached_within_ttl(memory_dirs: tuple[Path, Path]) -> None:
    """Repeated calls within TTL should return cached content without re-reading files."""
    prefs, profile = memory_dirs
    (prefs / "personal.md").write_text("---\n---\n\n# Personal\n\n- Wake: 07:30\n")
    reader = MemoryReader(preferences_dir=prefs, profile_dir=profile, cache_ttl_seconds=60)

    result1 = reader.get_preferences()
    # Modify file — but cache should still return old value
    (prefs / "personal.md").write_text("---\n---\n\n# Personal\n\n- Wake: 08:00\n")
    result2 = reader.get_preferences()
    assert result1 == result2  # cached
    assert "07:30" in result2


def test_preferences_refreshed_after_ttl(memory_dirs: tuple[Path, Path]) -> None:
    """After TTL expires, the reader should re-read files."""
    prefs, profile = memory_dirs
    (prefs / "personal.md").write_text("---\n---\n\n# Personal\n\n- Wake: 07:30\n")
    reader = MemoryReader(preferences_dir=prefs, profile_dir=profile, cache_ttl_seconds=0)

    reader.get_preferences()
    (prefs / "personal.md").write_text("---\n---\n\n# Personal\n\n- Wake: 08:00\n")
    time.sleep(0.01)  # TTL=0 means always-expired
    result2 = reader.get_preferences()
    assert "08:00" in result2  # refreshed
