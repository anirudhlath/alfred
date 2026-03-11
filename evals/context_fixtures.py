"""Context fixture loading — deserializes and renders ContextSnapshot fixtures for evals."""

from __future__ import annotations

import functools
import json
from pathlib import Path

from core.reflex.context_reader import render_snapshot
from sdk.alfred_sdk.context import ContextSnapshot

_CONTEXTS_DIR = Path(__file__).parent / "contexts"


@functools.cache
def load_context_text(fixture_name: str, contexts_dir: Path = _CONTEXTS_DIR) -> str:
    """Load a fixture file and return rendered Markdown for all services.

    The fixture is a JSON object mapping service names to ContextSnapshot dicts.
    Services are rendered in sorted order and concatenated with blank line separators.
    """
    fixture_path = contexts_dir / fixture_name
    raw: dict[str, object] = json.loads(fixture_path.read_text())

    parts: list[str] = []
    for service_name in sorted(raw):
        snapshot = ContextSnapshot.model_validate(raw[service_name])
        rendered = render_snapshot(snapshot)
        if rendered:
            parts.append(rendered)

    return "\n\n".join(parts)
