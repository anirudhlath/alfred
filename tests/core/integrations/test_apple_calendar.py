"""Tests for Apple Calendar integration adapter."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from core.integrations.apple_calendar import AppleCalendarAdapter
from core.integrations.base import IntegrationRequest
from core.integrations.registry import IntegrationRegistry


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    """Clear cached instances so each test gets a fresh adapter."""
    IntegrationRegistry._instances.pop("apple_calendar", None)


@pytest.fixture
def adapter() -> AppleCalendarAdapter:
    return AppleCalendarAdapter(
        caldav_url="https://caldav.icloud.com",
        username="test@icloud.com",
        password="app-specific-password",
    )


@pytest.fixture
def unconfigured_adapter() -> AppleCalendarAdapter:
    return AppleCalendarAdapter(caldav_url="", username="", password="")


@pytest.mark.asyncio
async def test_get_capabilities(adapter: AppleCalendarAdapter) -> None:
    caps = await adapter.get_capabilities()
    names = [c.name for c in caps]
    assert "get_today_events" in names
    assert "get_upcoming" in names


@pytest.mark.asyncio
async def test_health_check_no_connection(
    unconfigured_adapter: AppleCalendarAdapter,
) -> None:
    result = await unconfigured_adapter.health_check()
    assert result is False


@pytest.mark.asyncio
async def test_execute_unconfigured_returns_error(
    unconfigured_adapter: AppleCalendarAdapter,
) -> None:
    """execute() with empty URL returns error result, not an exception."""
    result = await unconfigured_adapter.execute(
        IntegrationRequest(action="get_today_events", params={})
    )
    assert result.confidence == 0.0
    assert "not configured" in result.data["error"]


@pytest.mark.asyncio
async def test_execute_caldav_not_installed(
    adapter: AppleCalendarAdapter,
) -> None:
    """If caldav package is missing, returns a graceful error."""
    with patch.object(adapter, "_get_client", side_effect=ImportError):
        result = await adapter.execute(IntegrationRequest(action="get_today_events", params={}))
    assert result.confidence == 0.0
    assert "not installed" in result.data["error"]


def test_credentials_schema_declared() -> None:
    """Adapter should declare its credential fields."""
    schema = AppleCalendarAdapter.credentials_schema
    assert "caldav_url" in schema.fields
    assert "username" in schema.fields
    assert "password" in schema.fields
    assert schema.fields["password"].field_type == "password"
    assert schema.fields["caldav_url"].field_type == "url"


@pytest.mark.asyncio
async def test_get_capabilities_includes_create_event(
    adapter: AppleCalendarAdapter,
) -> None:
    """create_event should be in the capabilities list."""
    caps = await adapter.get_capabilities()
    names = [c.name for c in caps]
    assert "create_event" in names
    create_cap = next(c for c in caps if c.name == "create_event")
    assert "summary" in create_cap.params_schema["properties"]
    assert "start" in create_cap.params_schema["properties"]
    assert "end" in create_cap.params_schema["properties"]


@pytest.mark.asyncio
async def test_create_event_unconfigured_returns_error(
    unconfigured_adapter: AppleCalendarAdapter,
) -> None:
    """create_event with empty URL returns not-configured error."""
    result = await unconfigured_adapter.execute(
        IntegrationRequest(
            action="create_event",
            params={
                "summary": "Test",
                "start": "2026-04-10T14:00:00-05:00",
                "end": "2026-04-10T17:30:00-05:00",
            },
        )
    )
    assert result.confidence == 0.0
    assert "not configured" in result.data["error"]


@pytest.mark.asyncio
async def test_create_event_missing_params(
    adapter: AppleCalendarAdapter,
) -> None:
    """create_event without required params returns error."""
    result = await adapter.execute(
        IntegrationRequest(action="create_event", params={"summary": "Test"})
    )
    assert result.confidence == 0.0
    assert "required" in result.data["error"]


@pytest.mark.asyncio
async def test_create_event_success(
    adapter: AppleCalendarAdapter,
) -> None:
    """create_event calls caldav save_event and returns created details."""
    mock_cal = MagicMock()
    mock_cal.get_display_name.return_value = "Home"
    mock_principal = MagicMock()
    mock_principal.calendars.return_value = [mock_cal]
    mock_client = MagicMock()
    mock_client.principal.return_value = mock_principal

    with (
        patch.object(adapter, "_get_client", return_value=mock_client),
        patch.object(type(adapter), "_supports_vevent", return_value=True),
    ):
        result = await adapter.execute(
            IntegrationRequest(
                action="create_event",
                params={
                    "summary": "Westcliff Weekend",
                    "start": "2026-04-10T14:00:00-05:00",
                    "end": "2026-04-10T17:30:00-05:00",
                    "location": "Westcliff Campus",
                },
            )
        )

    assert result.confidence == 0.9
    assert result.data["status"] == "created"
    assert result.data["summary"] == "Westcliff Weekend"
    assert result.data["calendar"] == "Home"
    mock_cal.save_event.assert_called_once()
    ical_str: str = mock_cal.save_event.call_args.args[0]
    assert "SUMMARY:Westcliff Weekend" in ical_str
    assert "LOCATION:Westcliff Campus" in ical_str
    # Verify UTC Z suffix is present (timezone preserved)
    assert "T190000Z" in ical_str  # 2:00 PM CDT = 19:00 UTC


def test_fetch_events_skips_non_vevent_entries(
    adapter: AppleCalendarAdapter,
) -> None:
    """_fetch_events skips calendar entries without vevent (e.g. VTODO)."""
    mock_vtodo = MagicMock(spec=[])
    mock_vevent_inner = MagicMock()
    mock_vevent_inner.summary.value = "Real Event"
    mock_vevent_inner.dtstart.value = "2026-04-10T14:00:00"
    mock_vevent_inner.dtend.value = "2026-04-10T15:00:00"
    mock_vevent_obj = MagicMock()
    mock_vevent_obj.vevent = mock_vevent_inner
    del mock_vevent_obj.vevent_list

    mock_entry_todo = MagicMock()
    mock_entry_todo.vobject_instance = mock_vtodo
    mock_entry_event = MagicMock()
    mock_entry_event.vobject_instance = mock_vevent_obj

    mock_cal = MagicMock()
    mock_cal.date_search.return_value = [mock_entry_todo, mock_entry_event]
    mock_principal = MagicMock()
    mock_principal.calendars.return_value = [mock_cal]
    mock_client = MagicMock()
    mock_client.principal.return_value = mock_principal

    with patch.object(adapter, "_get_client", return_value=mock_client):
        result = adapter._fetch_events(IntegrationRequest(action="get_today_events", params={}))

    assert len(result["events"]) == 1
    assert result["events"][0]["summary"] == "Real Event"


@pytest.mark.asyncio
async def test_create_event_malformed_datetime(
    adapter: AppleCalendarAdapter,
) -> None:
    """Malformed datetime strings return a friendly error."""
    result = await adapter.execute(
        IntegrationRequest(
            action="create_event",
            params={
                "summary": "Test",
                "start": "next Tuesday",
                "end": "next Wednesday",
            },
        )
    )
    assert result.confidence == 0.0
    assert "ISO 8601" in result.data["error"]


@pytest.mark.asyncio
async def test_create_event_end_before_start(
    adapter: AppleCalendarAdapter,
) -> None:
    """End time before start time returns an error."""
    result = await adapter.execute(
        IntegrationRequest(
            action="create_event",
            params={
                "summary": "Test",
                "start": "2026-04-10T17:00:00-05:00",
                "end": "2026-04-10T14:00:00-05:00",
            },
        )
    )
    assert result.confidence == 0.0
    assert "after start" in result.data["error"]


@pytest.mark.asyncio
async def test_create_event_no_calendars(
    adapter: AppleCalendarAdapter,
) -> None:
    """create_event with no calendars returns an error."""
    mock_principal = MagicMock()
    mock_principal.calendars.return_value = []
    mock_client = MagicMock()
    mock_client.principal.return_value = mock_principal

    with patch.object(adapter, "_get_client", return_value=mock_client):
        result = await adapter.execute(
            IntegrationRequest(
                action="create_event",
                params={
                    "summary": "Test",
                    "start": "2026-04-10T14:00:00-05:00",
                    "end": "2026-04-10T17:00:00-05:00",
                },
            )
        )
    assert result.confidence == 0.0
    assert "No calendars" in result.data["error"]


@pytest.mark.asyncio
async def test_unknown_action_returns_error(
    adapter: AppleCalendarAdapter,
) -> None:
    """Unknown action returns an error instead of falling through."""
    result = await adapter.execute(IntegrationRequest(action="purge_all", params={}))
    assert result.confidence == 0.0
    assert "Unknown action" in result.data["error"]


@pytest.mark.asyncio
async def test_get_capabilities_includes_delete_event(
    adapter: AppleCalendarAdapter,
) -> None:
    """delete_event should be in the capabilities list."""
    caps = await adapter.get_capabilities()
    names = [c.name for c in caps]
    assert "delete_event" in names


@pytest.mark.asyncio
async def test_delete_event_success(adapter: AppleCalendarAdapter) -> None:
    """delete_event removes matching events and returns count."""
    mock_event = MagicMock()
    mock_event.data = "BEGIN:VEVENT\nSUMMARY:Westcliff Session\nEND:VEVENT"
    mock_other = MagicMock()
    mock_other.data = "BEGIN:VEVENT\nSUMMARY:Dentist\nEND:VEVENT"
    # Event with "Westcliff" in description but NOT in summary — should NOT be deleted
    mock_desc_match = MagicMock()
    mock_desc_match.data = (
        "BEGIN:VEVENT\nSUMMARY:Team Meeting\nDESCRIPTION:Discuss Westcliff project\nEND:VEVENT"
    )

    mock_cal = MagicMock()
    mock_cal.search.return_value = [mock_event, mock_other, mock_desc_match]
    mock_principal = MagicMock()
    mock_principal.calendars.return_value = [mock_cal]
    mock_client = MagicMock()
    mock_client.principal.return_value = mock_principal

    with patch.object(adapter, "_get_client", return_value=mock_client):
        result = await adapter.execute(
            IntegrationRequest(
                action="delete_event",
                params={
                    "search": "Westcliff",
                    "start": "2026-04-10T00:00:00Z",
                    "end": "2026-04-14T00:00:00Z",
                },
            )
        )

    assert result.confidence == 0.9
    assert result.data["count"] == 1
    assert result.data["deleted"] == ["Westcliff Session"]
    mock_event.delete.assert_called_once()
    mock_other.delete.assert_not_called()
    mock_desc_match.delete.assert_not_called()  # Summary doesn't match


@pytest.mark.asyncio
async def test_delete_event_missing_params(adapter: AppleCalendarAdapter) -> None:
    """delete_event without required params returns error."""
    result = await adapter.execute(
        IntegrationRequest(action="delete_event", params={"search": "Test"})
    )
    assert result.confidence == 0.0
    assert "required" in result.data["error"]


@pytest.mark.asyncio
async def test_list_calendars(adapter: AppleCalendarAdapter) -> None:
    """list_calendars returns calendar names and types."""
    mock_cal_events = MagicMock()
    mock_cal_events.get_display_name.return_value = "Personal"
    mock_cal_reminders = MagicMock()
    mock_cal_reminders.get_display_name.return_value = "Reminders"

    mock_principal = MagicMock()
    mock_principal.calendars.return_value = [mock_cal_reminders, mock_cal_events]
    mock_client = MagicMock()
    mock_client.principal.return_value = mock_principal

    def fake_supports(cal: Any) -> bool:
        return cal is mock_cal_events

    with (
        patch.object(adapter, "_get_client", return_value=mock_client),
        patch.object(type(adapter), "_supports_vevent", side_effect=fake_supports),
    ):
        result = await adapter.execute(IntegrationRequest(action="list_calendars", params={}))

    assert result.confidence == 0.9
    assert result.data["count"] == 2
    cals = result.data["calendars"]
    assert cals[0]["name"] == "Reminders"
    assert cals[0]["type"] == "reminders/tasks"
    assert cals[1]["name"] == "Personal"
    assert cals[1]["type"] == "events"


@pytest.mark.asyncio
async def test_create_event_with_alerts(adapter: AppleCalendarAdapter) -> None:
    """create_event with alerts generates VALARM entries in iCal."""
    mock_cal = MagicMock()
    mock_cal.get_display_name.return_value = "Personal"
    mock_principal = MagicMock()
    mock_principal.calendars.return_value = [mock_cal]
    mock_client = MagicMock()
    mock_client.principal.return_value = mock_principal

    with (
        patch.object(adapter, "_get_client", return_value=mock_client),
        patch.object(type(adapter), "_supports_vevent", return_value=True),
    ):
        result = await adapter.execute(
            IntegrationRequest(
                action="create_event",
                params={
                    "summary": "Meeting",
                    "start": "2026-04-10T14:00:00Z",
                    "end": "2026-04-10T15:00:00Z",
                    "alerts": [30, 10],
                },
            )
        )

    assert result.confidence == 0.9
    assert result.data["alerts"] == ["30 min before", "10 min before"]
    ical_str: str = mock_cal.save_event.call_args.args[0]
    assert "BEGIN:VALARM" in ical_str
    assert "TRIGGER:-PT30M" in ical_str
    assert "TRIGGER:-PT10M" in ical_str


@pytest.mark.asyncio
async def test_create_event_with_calendar_name(adapter: AppleCalendarAdapter) -> None:
    """create_event with calendar_name picks the correct calendar."""
    mock_personal = MagicMock()
    mock_personal.get_display_name.return_value = "Personal"
    mock_college = MagicMock()
    mock_college.get_display_name.return_value = "College"

    mock_principal = MagicMock()
    mock_principal.calendars.return_value = [mock_personal, mock_college]
    mock_client = MagicMock()
    mock_client.principal.return_value = mock_principal

    with (
        patch.object(adapter, "_get_client", return_value=mock_client),
        patch.object(type(adapter), "_supports_vevent", return_value=True),
    ):
        result = await adapter.execute(
            IntegrationRequest(
                action="create_event",
                params={
                    "summary": "Lecture",
                    "start": "2026-04-10T14:00:00Z",
                    "end": "2026-04-10T15:00:00Z",
                    "calendar_name": "College",
                },
            )
        )

    assert result.confidence == 0.9
    assert result.data["calendar"] == "College"
    mock_college.save_event.assert_called_once()
    mock_personal.save_event.assert_not_called()
