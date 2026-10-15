"""Prompt names, the repo defaults and rendering."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.librarian.agents import (
    ANALYSE_INSTRUCTIONS,
    COMPRESS_INSTRUCTIONS,
    EXTRACT_INSTRUCTIONS,
    pattern_instructions,
    resolve_instructions,
)
from shared.prompts.names import (
    LIBRARIAN_ANALYSE,
    LIBRARIAN_COMPRESS,
    LIBRARIAN_EXTRACT,
    LIBRARIAN_PATTERNS,
    LIBRARIAN_RESOLVE,
    PROMPTS,
    SYSTEM2_LOCATION,
    SYSTEM2_PERSONALITY,
)
from shared.prompts.template import (
    DEFAULTS_DIR,
    PromptRef,
    RenderedPrompt,
    check_variables,
    load_default,
    render,
    repo_prompt,
    repo_text,
    unusable_reason,
    variables_in,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_every_prompt_has_a_default_using_exactly_its_variables() -> None:
    for name, declared in PROMPTS.items():
        assert variables_in(repo_text(name)) == declared, name


def test_the_defaults_directory_holds_only_known_prompts() -> None:
    assert {p.stem for p in DEFAULTS_DIR.glob("*.md")} == set(PROMPTS)


def test_load_default_strips_exactly_one_trailing_newline(tmp_path: Path) -> None:
    path = tmp_path / "p.md"
    path.write_text("line\n\n", encoding="utf-8")
    assert load_default(path) == "line\n"


def test_repo_prompt_renders_every_occurrence() -> None:
    prompt = repo_prompt(SYSTEM2_LOCATION, area="kitchen")
    assert prompt.text.count("kitchen") == 2
    assert "{{" not in prompt.text
    assert (prompt.name, prompt.version, prompt.source) == (SYSTEM2_LOCATION, None, "repo")


def test_variables_tolerate_spaces_inside_the_braces() -> None:
    assert variables_in("a {{ area }} b {{level}}") == frozenset({"area", "level"})
    assert render("at {{ area }}", {"area": "hall"}) == "at hall"


@pytest.mark.parametrize(
    "variables", [{}, {"area": "x", "extra": "y"}, {"room": "x"}], ids=["missing", "extra", "wrong"]
)
def test_check_variables_wants_exactly_the_declared_set(variables: dict[str, object]) -> None:
    with pytest.raises(ValueError, match=SYSTEM2_LOCATION):
        check_variables(SYSTEM2_LOCATION, variables)


def test_an_unknown_prompt_name_is_a_value_error() -> None:
    with pytest.raises(ValueError, match="no-such-prompt"):
        check_variables("no-such-prompt", {})


def test_unusable_reason() -> None:
    assert unusable_reason(SYSTEM2_PERSONALITY, "text", "You are Alfred.") is None
    assert unusable_reason(SYSTEM2_PERSONALITY, "chat", None) == "not a text prompt"
    reason = unusable_reason(SYSTEM2_LOCATION, "text", "at {{area}} in {{room}}")
    assert reason is not None and "room" in reason


def test_refs_name_the_version_or_the_repo() -> None:
    assert str(PromptRef("p", 3, "langfuse")) == "p@3"
    assert str(PromptRef("p", None, "repo")) == "p@repo"
    assert RenderedPrompt("t", "p", 3, "redis").ref == PromptRef("p", 3, "redis")


# Removed in Task 6, when the Librarian's builders are deleted and the defaults become
# the only source.
def test_librarian_defaults_match_todays_builders() -> None:
    assert repo_prompt(LIBRARIAN_ANALYSE).text == ANALYSE_INSTRUCTIONS
    assert repo_prompt(LIBRARIAN_EXTRACT).text == EXTRACT_INSTRUCTIONS
    assert repo_prompt(LIBRARIAN_COMPRESS).text == COMPRESS_INSTRUCTIONS
    resolve = repo_prompt(LIBRARIAN_RESOLVE, min_observations=5, min_days=14)
    assert resolve.text == resolve_instructions(5, 14)
    patterns = repo_prompt(
        LIBRARIAN_PATTERNS, min_occurrences=3, min_days=7, tz_name="America/Los_Angeles"
    )
    assert patterns.text == pattern_instructions(3, 7, "America/Los_Angeles")
