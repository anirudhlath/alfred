"""Integration capabilities offered to System 2 as tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IntegrationTool:
    """One capability as a tool: its wire name, where it runs, and its schema."""

    name: str
    integration: str
    capability: str
    description: str
    schema: dict[str, Any]
