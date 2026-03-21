"""Tests for notification schema models."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from core.notifications.schema import DNDStatus, Notification, Urgency


class TestUrgency:
    def test_enum_values(self) -> None:
        assert Urgency.INFORMATIONAL == "informational"
        assert Urgency.IMPORTANT == "important"
        assert Urgency.URGENT == "urgent"

    def test_enum_from_string(self) -> None:
        assert Urgency("informational") is Urgency.INFORMATIONAL


class TestNotification:
    def test_minimal_creation(self) -> None:
        n = Notification(
            title="Test",
            body="Hello",
            urgency=Urgency.INFORMATIONAL,
            source="test",
        )
        assert n.title == "Test"
        assert n.notification_id  # auto-generated UUID
        assert n.timestamp  # auto-generated datetime

    def test_rejects_invalid_urgency(self) -> None:
        with pytest.raises(ValidationError):
            Notification(
                title="Test",
                body="Hello",
                urgency="invalid",  # type: ignore[arg-type]
                source="test",
            )

    def test_serialization_roundtrip(self) -> None:
        n = Notification(
            title="Budget",
            body="80% used",
            urgency=Urgency.URGENT,
            source="cost_tracker",
            timestamp=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        )
        data = n.model_dump_json()
        restored = Notification.model_validate_json(data)
        assert restored.title == n.title
        assert restored.urgency is Urgency.URGENT


class TestDNDStatus:
    def test_inactive_default(self) -> None:
        status = DNDStatus(active=False)
        assert not status.active
        assert status.reason is None
        assert status.source is None
        assert status.until is None

    def test_active_with_fields(self) -> None:
        status = DNDStatus(
            active=True,
            reason="User requested",
            source="manual",
            until=datetime(2026, 3, 20, 15, 0, tzinfo=UTC),
        )
        assert status.active
        assert status.source == "manual"
