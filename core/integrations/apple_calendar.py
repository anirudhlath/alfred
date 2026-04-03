"""Apple Calendar integration adapter — CalDAV protocol."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from core.integrations.base import (
    CredentialField,
    CredentialSchema,
    Integration,
    IntegrationCapability,
    IntegrationRequest,
    IntegrationResult,
)
from core.integrations.registry import IntegrationRegistry
from core.integrations.sanitizer import sanitize_response

logger = logging.getLogger(__name__)

_MAX_DELETIONS = 20
_MAX_ALERT_MINUTES = 10080  # 7 days


def _format_ical_dt(dt: datetime) -> str:
    """Format a datetime for iCal.

    All offset-aware datetimes are converted to UTC with ``Z`` suffix.
    Naive datetimes are stored as floating (local time).
    """
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return dt.strftime("%Y%m%dT%H%M%S")


def _escape_ical_text(text: str) -> str:
    """Escape text per RFC 5545 Section 3.3.11."""
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _build_vevent_ical(
    *,
    uid: str,
    summary: str,
    dtstart: datetime,
    dtend: datetime,
    location: str = "",
    description: str = "",
    alerts: list[int] | None = None,
) -> str:
    """Build a minimal VCALENDAR/VEVENT iCal string with correct timezone.

    ``alerts`` is a list of minutes-before-event for VALARM entries.
    """
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Alfred//EN",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
        f"DTSTART:{_format_ical_dt(dtstart)}",
        f"DTEND:{_format_ical_dt(dtend)}",
        f"SUMMARY:{_escape_ical_text(summary)}",
    ]
    if location:
        lines.append(f"LOCATION:{_escape_ical_text(location)}")
    if description:
        lines.append(f"DESCRIPTION:{_escape_ical_text(description)}")
    for minutes in alerts or []:
        lines.extend(
            [
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                f"DESCRIPTION:{_escape_ical_text(summary)}",
                f"TRIGGER:-PT{minutes}M",
                "END:VALARM",
            ]
        )
    lines.extend(["END:VEVENT", "END:VCALENDAR"])
    return "\r\n".join(lines)


def _error_result(message: str) -> IntegrationResult:
    return IntegrationResult(
        data={"error": message},
        freshness=datetime.now(UTC),
        confidence=0.0,
    )


def _parse_date_range(
    params: dict[str, Any],
) -> tuple[datetime, datetime] | IntegrationResult:
    """Parse and validate start/end ISO 8601 strings from params.

    Returns ``(dtstart, dtend)`` on success or an ``IntegrationResult``
    error on failure.
    """
    try:
        dtstart = datetime.fromisoformat(params["start"])
        dtend = datetime.fromisoformat(params["end"])
    except (ValueError, KeyError):
        return _error_result("start and end must be valid ISO 8601 datetime strings")
    return dtstart, dtend


def _sanitize_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Run sanitize_response, ensuring the result is always a dict."""
    clean = sanitize_response(data)
    return clean if isinstance(clean, dict) else {"data": clean}


@IntegrationRegistry.register()
class AppleCalendarAdapter(Integration):
    """Fetches calendar events from Apple Calendar via CalDAV."""

    name = "apple_calendar"
    category = "calendar"

    credentials_schema = CredentialSchema(
        fields={
            "caldav_url": CredentialField(
                label="CalDAV URL",
                field_type="url",
                default="https://caldav.icloud.com",
                help_text=(
                    "Pre-filled for iCloud. Only change if using"
                    " Google Calendar or a self-hosted server"
                ),
            ),
            "username": CredentialField(
                label="Apple ID",
                placeholder="you@icloud.com",
                help_text="Your Apple ID email address used for iCloud sign-in",
            ),
            "password": CredentialField(
                label="App-Specific Password",
                field_type="password",
                help_text=(
                    "Go to appleid.apple.com > Sign-In and Security"
                    " > App-Specific Passwords > Generate"
                ),
            ),
        }
    )

    def __init__(
        self,
        caldav_url: str = "",
        username: str = "",
        password: str = "",
    ) -> None:
        self._url = caldav_url
        self._username = username
        self._password = password

    async def get_capabilities(self) -> list[IntegrationCapability]:
        return [
            IntegrationCapability(
                name="list_calendars",
                description="List all calendars on the account with their types",
                params_schema={"type": "object", "properties": {}},
            ),
            IntegrationCapability(
                name="get_today_events",
                description="Get today's calendar events",
                params_schema={"type": "object", "properties": {}},
            ),
            IntegrationCapability(
                name="get_upcoming",
                description="Get events for the next N days",
                params_schema={
                    "type": "object",
                    "properties": {"days": {"type": "integer", "default": 3}},
                },
            ),
            IntegrationCapability(
                name="create_event",
                description="Create a new calendar event with optional alerts",
                params_schema={
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "Event title",
                        },
                        "start": {
                            "type": "string",
                            "description": "Start time in ISO 8601 format",
                        },
                        "end": {
                            "type": "string",
                            "description": "End time in ISO 8601 format",
                        },
                        "location": {
                            "type": "string",
                            "description": "Event location",
                        },
                        "description": {
                            "type": "string",
                            "description": "Event description",
                        },
                        "calendar_name": {
                            "type": "string",
                            "description": (
                                "Name of the calendar to add the event to."
                                " Use list_calendars to see available options."
                                " Defaults to the first event calendar."
                            ),
                        },
                        "alerts": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": (
                                "List of alert times in minutes before the event."
                                " E.g. [30, 10] for alerts at 30 and 10 minutes before."
                            ),
                        },
                    },
                    "required": ["summary", "start", "end"],
                },
            ),
            IntegrationCapability(
                name="delete_event",
                description=(
                    "Delete calendar events matching a search term"
                    " (matched against event title only) within a date range."
                    f" Capped at {_MAX_DELETIONS} deletions per call."
                ),
                params_schema={
                    "type": "object",
                    "properties": {
                        "search": {
                            "type": "string",
                            "description": "Text to match in event summary/title",
                        },
                        "start": {
                            "type": "string",
                            "description": "Start of date range (ISO 8601)",
                        },
                        "end": {
                            "type": "string",
                            "description": "End of date range (ISO 8601)",
                        },
                        "dry_run": {
                            "type": "boolean",
                            "description": (
                                "If true, return matching events without deleting."
                                " Use this to preview before deleting."
                            ),
                        },
                    },
                    "required": ["search", "start", "end"],
                },
            ),
        ]

    async def execute(self, request: IntegrationRequest) -> IntegrationResult:
        if not self._url:
            return _error_result("CalDAV not configured")

        loop = asyncio.get_running_loop()

        try:
            match request.action:
                case "list_calendars":
                    result = await loop.run_in_executor(None, self._list_calendars)
                    return IntegrationResult(
                        data=_sanitize_dict(result),
                        freshness=datetime.now(UTC),
                        confidence=0.9,
                    )

                case "create_event":
                    return await self._handle_create_event(request, loop)

                case "delete_event":
                    return await self._handle_delete_event(request, loop)

                case "get_today_events" | "get_upcoming":
                    events = await loop.run_in_executor(None, self._fetch_events, request)
                    return IntegrationResult(
                        data=_sanitize_dict(events),
                        freshness=datetime.now(UTC),
                        confidence=0.9,
                    )

                case _:
                    return _error_result(f"Unknown action: {request.action}")
        except ImportError:
            return _error_result("caldav package not installed")
        except Exception as e:
            logger.error("Calendar operation failed: %s", e)
            return _error_result(str(e))

    async def _handle_create_event(
        self,
        request: IntegrationRequest,
        loop: asyncio.AbstractEventLoop,
    ) -> IntegrationResult:
        for field in ("summary", "start", "end"):
            if not request.params.get(field):
                return _error_result("summary, start, and end are required")
        parsed = _parse_date_range(request.params)
        if isinstance(parsed, IntegrationResult):
            return parsed
        dtstart, dtend = parsed
        if dtend <= dtstart:
            return _error_result("end must be after start")

        alerts: list[int] | None = None
        alerts_raw = request.params.get("alerts")
        if isinstance(alerts_raw, list):
            try:
                alerts = [int(a) for a in alerts_raw]
                if any(a < 0 or a > _MAX_ALERT_MINUTES for a in alerts):
                    return _error_result(f"alert values must be 0-{_MAX_ALERT_MINUTES} minutes")
            except (ValueError, TypeError):
                return _error_result("alerts must be a list of integers")

        result = await loop.run_in_executor(
            None,
            self._create_event,
            request.params["summary"],
            dtstart,
            dtend,
            request.params.get("location", ""),
            request.params.get("description", ""),
            request.params.get("calendar_name", ""),
            alerts,
        )
        if "error" in result:
            return _error_result(result["error"])
        return IntegrationResult(
            data=_sanitize_dict(result),
            freshness=datetime.now(UTC),
            confidence=0.9,
        )

    async def _handle_delete_event(
        self,
        request: IntegrationRequest,
        loop: asyncio.AbstractEventLoop,
    ) -> IntegrationResult:
        for field in ("search", "start", "end"):
            if not request.params.get(field):
                return _error_result("search, start, and end are required")
        parsed = _parse_date_range(request.params)
        if isinstance(parsed, IntegrationResult):
            return parsed
        dtstart, dtend = parsed

        dry_run = bool(request.params.get("dry_run", False))

        result = await loop.run_in_executor(
            None,
            self._delete_events,
            request.params["search"],
            dtstart,
            dtend,
            dry_run,
        )
        if "error" in result:
            return _error_result(result["error"])
        return IntegrationResult(
            data=_sanitize_dict(result),
            freshness=datetime.now(UTC),
            confidence=0.9,
        )

    def _get_client(self) -> Any:
        """Build a CalDAV client (sync)."""
        import caldav

        return caldav.DAVClient(  # type: ignore[operator]
            url=self._url,
            username=self._username,
            password=self._password,
        )

    def _get_calendars(self) -> list[Any]:
        """Fetch all calendars from the CalDAV server (sync)."""
        return self._get_client().principal().calendars()  # type: ignore[no-any-return]

    @staticmethod
    def _supports_vevent(cal: Any) -> bool:
        """Check if a CalDAV calendar supports VEVENT components."""
        try:
            from caldav.elements import cdav

            props = cal.get_properties([cdav.SupportedCalendarComponentSet()])
            for _key, value in props.items():
                if isinstance(value, list) and "VEVENT" in value:
                    return True
            return False
        except Exception:
            return True  # Assume writable if we can't determine

    @staticmethod
    def _cal_display_name(cal: Any) -> str:
        if hasattr(cal, "get_display_name"):
            return str(cal.get_display_name())
        return str(getattr(cal, "name", "default"))

    def _find_event_calendar(
        self, calendars: list[Any], calendar_name: str
    ) -> dict[str, Any] | Any:
        """Find a VEVENT-capable calendar, optionally by name.

        Returns the calendar object on success or an error dict on failure.
        """
        event_calendars = [c for c in calendars if self._supports_vevent(c)]
        if not event_calendars:
            return {"error": "No writable event calendars found"}

        if not calendar_name:
            return event_calendars[0]

        for c in event_calendars:
            if self._cal_display_name(c).lower() == calendar_name.lower():
                return c

        available = [self._cal_display_name(c) for c in event_calendars]
        return {
            "error": (f"Calendar '{calendar_name}' not found. Available: {', '.join(available)}")
        }

    def _list_calendars(self) -> dict[str, Any]:
        """List all calendars with their types."""
        calendars = self._get_calendars()
        result: list[dict[str, str]] = []
        for cal in calendars:
            name = self._cal_display_name(cal)
            supports_vevent = self._supports_vevent(cal)
            result.append(
                {
                    "name": name,
                    "type": "events" if supports_vevent else "reminders/tasks",
                }
            )
        return {"calendars": result, "count": len(result)}

    def _create_event(
        self,
        summary: str,
        dtstart: datetime,
        dtend: datetime,
        location: str,
        description: str,
        calendar_name: str,
        alerts: list[int] | None,
    ) -> dict[str, Any]:
        """Sync CalDAV event creation (runs in executor)."""
        calendars = self._get_calendars()
        if not calendars:
            return {"error": "No calendars found on this account"}

        cal_or_err = self._find_event_calendar(calendars, calendar_name)
        if isinstance(cal_or_err, dict):
            return cal_or_err
        cal = cal_or_err

        uid = str(uuid4())
        ical = _build_vevent_ical(
            uid=uid,
            summary=summary,
            dtstart=dtstart,
            dtend=dtend,
            location=location,
            description=description,
            alerts=alerts,
        )
        cal.save_event(ical)

        return {
            "status": "created",
            "uid": uid,
            "summary": summary,
            "start": str(dtstart),
            "end": str(dtend),
            "calendar": self._cal_display_name(cal),
            "alerts": [f"{m} min before" for m in (alerts or [])],
        }

    def _delete_events(
        self,
        search: str,
        dtstart: datetime,
        dtend: datetime,
        dry_run: bool,
    ) -> dict[str, Any]:
        """Sync CalDAV event deletion (runs in executor).

        Only searches VEVENT-capable calendars. Matches against SUMMARY only.
        Capped at ``_MAX_DELETIONS`` to prevent accidental bulk deletion.
        """
        calendars = self._get_calendars()
        # Only search event calendars — skip reminder lists
        event_calendars = [c for c in calendars if self._supports_vevent(c)]

        matched: list[dict[str, str]] = []
        for cal in event_calendars:
            cal_name = self._cal_display_name(cal)
            try:
                events = cal.search(start=dtstart, end=dtend, event=True, expand=False)
            except Exception as e:
                logger.warning("Calendar search failed for '%s': %s", cal_name, e)
                continue
            for event in events:
                data = event.data or ""
                summary = "unknown"
                for line in data.split("\n"):
                    if line.startswith("SUMMARY"):
                        summary = line.split(":", 1)[1].strip()
                        break
                if search.lower() not in summary.lower():
                    continue
                matched.append({"summary": summary, "calendar": cal_name})
                if dry_run:
                    continue
                if len(matched) > _MAX_DELETIONS:
                    return {
                        "error": (
                            f"Too many matches ({len(matched)}+)."
                            f" Cap is {_MAX_DELETIONS}."
                            " Use a more specific search term."
                        )
                    }
                logger.info(
                    "Deleting calendar event: '%s' from '%s'",
                    summary,
                    cal_name,
                )
                event.delete()

        if dry_run:
            return {
                "status": "dry_run",
                "count": len(matched),
                "matches": matched,
            }
        return {
            "status": "deleted",
            "count": len(matched),
            "deleted": [m["summary"] for m in matched],
        }

    def _fetch_events(self, request: IntegrationRequest) -> dict[str, Any]:
        """Sync CalDAV fetch (runs in executor)."""
        calendars = self._get_calendars()

        days = request.params.get("days", 1) if request.action == "get_upcoming" else 1
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=days)

        all_events: list[dict[str, str]] = []
        for cal in calendars:
            for event in cal.date_search(start=start, end=end, expand=True):
                vobj = event.vobject_instance
                if not hasattr(vobj, "vevent") and not hasattr(vobj, "vevent_list"):
                    continue
                vevents = vobj.vevent_list if hasattr(vobj, "vevent_list") else [vobj.vevent]
                for vevent in vevents:
                    all_events.append(
                        {
                            "summary": (
                                str(vevent.summary.value)
                                if hasattr(vevent, "summary")
                                else "Untitled"
                            ),
                            "start": (
                                str(vevent.dtstart.value) if hasattr(vevent, "dtstart") else ""
                            ),
                            "end": (str(vevent.dtend.value) if hasattr(vevent, "dtend") else ""),
                        }
                    )

        return {"events": all_events, "calendar_count": len(calendars)}

    async def health_check(self) -> bool:
        if not self._url:
            return False
        try:
            loop = asyncio.get_running_loop()

            def check() -> bool:
                self._get_client().principal()
                return True

            return await loop.run_in_executor(None, check)
        except Exception:
            return False
