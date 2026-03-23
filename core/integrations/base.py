"""Integration base classes — ABC and Pydantic schemas for data-fetching adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime  # noqa: TC003
from typing import Any

from pydantic import BaseModel


class IntegrationRequest(BaseModel):
    """Request to an integration adapter."""

    action: str
    params: dict[str, Any] = {}


class IntegrationResult(BaseModel):
    """Result from an integration adapter."""

    data: dict[str, Any]
    freshness: datetime
    confidence: float  # 0.0-1.0


class IntegrationCapability(BaseModel):
    """Describes one action an integration can perform."""

    name: str
    description: str
    params_schema: dict[str, Any]


class CredentialField(BaseModel):
    """Describes one credential input field for an integration adapter."""

    label: str
    field_type: str = "text"  # "text" | "password" | "url"
    required: bool = True
    placeholder: str = ""
    help_text: str = ""
    transient: bool = False  # If True, value is passed to adapter but not persisted


class CredentialSchema(BaseModel):
    """Describes all credential fields for an integration adapter."""

    fields: dict[str, CredentialField]


class Integration(ABC):
    """Abstract base class for data-fetching integrations.

    Each adapter handles its own auth, has a health_check(),
    and returns typed results with freshness and confidence.
    """

    name: str
    category: str  # "calendar", "health", "finance", "weather"
    credentials_schema: CredentialSchema = CredentialSchema(fields={})

    @abstractmethod
    async def get_capabilities(self) -> list[IntegrationCapability]: ...

    @abstractmethod
    async def execute(self, request: IntegrationRequest) -> IntegrationResult: ...

    @abstractmethod
    async def health_check(self) -> bool: ...
