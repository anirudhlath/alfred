"""Notification models — schema for the proactive notification system."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class Urgency(StrEnum):
    """Notification urgency level. Determines channel routing.

    Members are declared in ascending order so that list(Urgency) is ordered.
    Use Urgency.URGENT != notification.urgency for DND bypass checks.
    """

    INFORMATIONAL = "informational"
    IMPORTANT = "important"
    URGENT = "urgent"


class Notification(BaseModel):
    """A notification to be dispatched to one or more channels."""

    notification_id: str = Field(default_factory=lambda: str(uuid4()))
    title: str
    body: str
    urgency: Urgency
    source: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DNDStatus(BaseModel):
    """Current Do-Not-Disturb state."""

    active: bool
    reason: str | None = None
    source: str | None = None  # "manual" | "calendar"
    until: datetime | None = None
