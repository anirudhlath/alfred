"""Apple Calendar integration adapter — CalDAV protocol."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

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
                help_text="Pre-filled for iCloud. Only change if using Google Calendar or a self-hosted server",
            ),
            "username": CredentialField(
                label="Apple ID",
                placeholder="you@icloud.com",
                help_text="Your Apple ID email address used for iCloud sign-in",
            ),
            "password": CredentialField(
                label="App-Specific Password",
                field_type="password",
                help_text="Go to appleid.apple.com > Sign-In and Security > App-Specific Passwords > Generate",
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
        ]

    async def execute(self, request: IntegrationRequest) -> IntegrationResult:
        if not self._url:
            return IntegrationResult(
                data={"error": "CalDAV not configured"},
                freshness=datetime.now(UTC),
                confidence=0.0,
            )

        try:
            import asyncio
            import importlib.util

            if importlib.util.find_spec("caldav") is None:
                raise ImportError("caldav not installed")

            # caldav is sync — run in executor
            loop = asyncio.get_running_loop()
            events = await loop.run_in_executor(None, self._fetch_events, request)
            clean = sanitize_response(events)
            return IntegrationResult(
                data=clean if isinstance(clean, dict) else {"events": clean},
                freshness=datetime.now(UTC),
                confidence=0.9,
            )
        except ImportError:
            return IntegrationResult(
                data={"error": "caldav package not installed"},
                freshness=datetime.now(UTC),
                confidence=0.0,
            )
        except Exception as e:
            logger.error("Calendar fetch failed: %s", e)
            return IntegrationResult(
                data={"error": str(e)},
                freshness=datetime.now(UTC),
                confidence=0.0,
            )

    def _fetch_events(self, request: IntegrationRequest) -> dict[str, Any]:
        """Sync CalDAV fetch (runs in executor)."""
        import caldav

        client = caldav.DAVClient(  # type: ignore[operator]
            url=self._url, username=self._username, password=self._password
        )
        principal = client.principal()
        calendars = principal.calendars()

        days = request.params.get("days", 1) if request.action == "get_upcoming" else 1
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=days)

        all_events: list[dict[str, str]] = []
        for cal in calendars:
            for event in cal.date_search(start=start, end=end, expand=True):
                vobj = event.vobject_instance
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
            import asyncio

            import caldav

            loop = asyncio.get_running_loop()

            def check() -> bool:
                client = caldav.DAVClient(  # type: ignore[operator]
                    url=self._url, username=self._username, password=self._password
                )
                client.principal()
                return True

            return await loop.run_in_executor(None, check)
        except Exception:
            return False
