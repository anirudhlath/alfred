"""Prompt text: ``{{variable}}`` rendering, the repo defaults and what a served prompt is."""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

from shared.prompts.names import PROMPTS

if TYPE_CHECKING:
    from collections.abc import Mapping

PromptSource = Literal["langfuse", "redis", "repo"]

DEFAULTS_DIR: Final = Path(__file__).parent / "defaults"
# Langfuse's variable syntax.
_VARIABLE: Final = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


@dataclass(frozen=True)
class PromptRef:
    """Which version of a prompt was served; ``version`` is None for a repo default."""

    name: str
    version: int | None
    source: PromptSource

    def __str__(self) -> str:
        return f"{self.name}@{'repo' if self.version is None else self.version}"


@dataclass(frozen=True)
class RenderedPrompt:
    """A prompt's text with its variables filled in, and where it came from."""

    text: str
    name: str
    version: int | None
    source: PromptSource

    @property
    def ref(self) -> PromptRef:
        return PromptRef(self.name, self.version, self.source)


def variables_in(text: str) -> frozenset[str]:
    """The ``{{variable}}`` names *text* uses."""
    return frozenset(_VARIABLE.findall(text))


def check_variables(name: str, variables: Mapping[str, object]) -> None:
    """Raise ``ValueError`` unless *variables* are exactly what prompt *name* declares.

    A mismatch is a bug in the caller, so it fails loudly rather than serving a prompt
    with a hole in it.
    """
    declared = PROMPTS.get(name)
    if declared is None:
        raise ValueError(f"unknown prompt {name!r}")
    if set(variables) != declared:
        raise ValueError(f"prompt {name!r} takes {sorted(declared)}, got {sorted(variables)}")


def render(text: str, variables: Mapping[str, object]) -> str:
    """Fill every ``{{variable}}`` in *text*; the caller has checked the names."""
    return _VARIABLE.sub(lambda m: str(variables[m.group(1)]), text)


def load_default(path: Path) -> str:
    """A default file's text, without the one newline that terminates the file."""
    return path.read_text(encoding="utf-8").removesuffix("\n")


@functools.cache
def repo_text(name: str) -> str:
    """Prompt *name*'s repo default, unrendered."""
    if name not in PROMPTS:
        raise ValueError(f"unknown prompt {name!r}")
    return load_default(DEFAULTS_DIR / f"{name}.md")


def repo_prompt(name: str, **variables: object) -> RenderedPrompt:
    """Prompt *name*'s repo default, rendered."""
    check_variables(name, variables)
    return RenderedPrompt(render(repo_text(name), variables), name, None, "repo")


def unusable_reason(name: str, prompt_type: str, text: str | None) -> str | None:
    """Why a fetched version of prompt *name* cannot be served, or None if it can."""
    if prompt_type != "text" or text is None:
        return "not a text prompt"
    undeclared = variables_in(text) - PROMPTS[name]
    if undeclared:
        return f"uses undeclared variables {sorted(undeclared)}"
    return None
