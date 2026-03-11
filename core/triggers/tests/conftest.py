"""Conftest for trigger tests — ensures trigger type modules can be re-imported per test."""

from __future__ import annotations

import sys

import pytest

_TRIGGER_TYPE_MODULES = [
    "core.triggers.types",
    "core.triggers.types.time",
]


@pytest.fixture(autouse=True)
def _reset_trigger_type_modules() -> None:
    """Remove trigger type modules from sys.modules so fixture imports re-execute decorators."""
    for mod in _TRIGGER_TYPE_MODULES:
        sys.modules.pop(mod, None)
