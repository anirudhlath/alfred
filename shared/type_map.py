"""Shared Python → JSON Schema type mapping."""

from __future__ import annotations

from typing import Any

from sdk.alfred_sdk.feature import JSON_TYPE_BY_PYTHON_NAME

# Alias kept for core/conscious/engine.py until it reads the SDK's input_schema.
PYTHON_TO_JSON_SCHEMA = JSON_TYPE_BY_PYTHON_NAME


def friendly_type(annotation: Any) -> str:
    """Convert a Python type annotation to an LLM-friendly string.

    Handles Optional/Union wrappers and provides datetime formatting hints.
    """
    raw = getattr(annotation, "__name__", str(annotation))
    # Strip Optional/Union wrappers
    base = raw.replace("typing.", "").split("|")[0].strip().split("[")[0].strip()
    # datetime.datetime -> ISO 8601 string
    if "datetime" in base:
        return "string (ISO 8601, e.g. 2026-03-20T08:30:00Z)"
    return JSON_TYPE_BY_PYTHON_NAME.get(base, base)
