"""Google Calendar agenda briefing boot task for DeskPilot."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time
import sys
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from pydantic import BaseModel, Field

from deskpilot.config import CalendarConfig

CALENDAR_READONLY_SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"
CALENDAR_SCOPES = [CALENDAR_READONLY_SCOPE]


class CalendarEventItem(BaseModel):
    """Normalized Google Calendar event item for briefing."""

    id: str
    summary: str
    start_time: str
    end_time: str
    is_all_day: bool = False
    location: str | None = None
    html_link: str | None = None


class CalendarAgendaResult(BaseModel):
    """Aggregated result of today's Google Calendar agenda briefing."""

    events: list[CalendarEventItem] = Field(default_factory=list)
    total_count: int = 0
    date_str: str = ""
    is_configured: bool = True
    error: str | None = None


def _fetch_agenda_sync(
    config: CalendarConfig | None = None,
    service: Any = None,
    target_date: date | None = None,
    allow_interactive: bool | None = None,
) -> CalendarAgendaResult:
    """Synchronously retrieve and normalize today's calendar agenda."""
    if config is None:
        config = CalendarConfig()

    today_date = target_date if target_date is not None else datetime.now().date()
    today_str = today_date.isoformat()

    if not config.enabled:
        return CalendarAgendaResult(
            events=[],
            total_count=0,
            date_str=today_str,
            is_configured=False,
            error=None,
        )

    if service is None:
        token_path = config.get_resolved_token_path()
        creds_path = config.get_resolved_credentials_path()

        creds = None
        if not token_path.exists():
            if not creds_path.exists():
                return CalendarAgendaResult(
                    events=[],
                    total_count=0,
                    date_str=today_str,
                    is_configured=False,
                    error=f"Google Calendar credentials not found at {creds_path} and token not found at {token_path}",
                )

            is_interactive = (
                allow_interactive
                if allow_interactive is not None
                else (sys.stdin is not None and hasattr(sys.stdin, "isatty") and sys.stdin.isatty())
            )
            if is_interactive:
                try:
                    from google_auth_oauthlib.flow import InstalledAppFlow

                    flow = InstalledAppFlow.from_client_secrets_file(
                        str(creds_path), scopes=CALENDAR_SCOPES
                    )
                    creds = flow.run_local_server(port=0)
                    token_path.parent.mkdir(parents=True, exist_ok=True)
                    token_path.write_text(creds.to_json(), encoding="utf-8")
                except Exception as e:
                    return CalendarAgendaResult(
                        events=[],
                        total_count=0,
                        date_str=today_str,
                        is_configured=False,
                        error=f"Google Calendar OAuth authorization failed: {e}",
                    )
            else:
                return CalendarAgendaResult(
                    events=[],
                    total_count=0,
                    date_str=today_str,
                    is_configured=False,
                    error=f"Google Calendar token not found at {token_path}. Run OAuth authorization flow to generate token.",
                )

        if creds is None:
            try:
                creds = Credentials.from_authorized_user_file(str(token_path), scopes=CALENDAR_SCOPES)
            except Exception as e:
                return CalendarAgendaResult(
                    events=[],
                    total_count=0,
                    date_str=today_str,
                    is_configured=False,
                    error=f"Failed to load Google Calendar token from {token_path}: {e}",
                )

        if creds and not creds.valid:
            if creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    try:
                        token_path.write_text(creds.to_json(), encoding="utf-8")
                    except OSError:
                        pass
                except Exception as e:
                    return CalendarAgendaResult(
                        events=[],
                        total_count=0,
                        date_str=today_str,
                        is_configured=False,
                        error=f"Failed to refresh expired Google Calendar token: {e}",
                    )
            else:
                return CalendarAgendaResult(
                    events=[],
                    total_count=0,
                    date_str=today_str,
                    is_configured=False,
                    error="Google Calendar token is expired or invalid without refresh token",
                )

        try:
            service = build("calendar", "v3", credentials=creds, static_discovery=False)
        except Exception as e:
            return CalendarAgendaResult(
                events=[],
                total_count=0,
                date_str=today_str,
                is_configured=True,
                error=f"Failed to build Google Calendar service: {e}",
            )

    # Service is initialized; retrieve today's bounds
    now = datetime.now().astimezone()
    tz = now.tzinfo
    start_of_day = datetime.combine(today_date, time.min, tzinfo=tz)
    end_of_day = datetime.combine(today_date, time.max, tzinfo=tz)
    time_min = start_of_day.isoformat()
    time_max = end_of_day.isoformat()

    try:
        events_result = (
            service.events()
            .list(
                calendarId=config.calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                maxResults=config.max_results,
            )
            .execute()
        )
    except Exception as e:
        return CalendarAgendaResult(
            events=[],
            total_count=0,
            date_str=today_str,
            is_configured=True,
            error=f"Google Calendar API query failed: {e}",
        )

    items = events_result.get("items", []) if isinstance(events_result, dict) else []
    parsed_events: list[CalendarEventItem] = []
    for item in items:
        start = item.get("start") or {}
        end = item.get("end") or {}

        if "dateTime" in start:
            is_all_day = False
            start_time = str(start.get("dateTime", ""))
            end_time = str(end.get("dateTime", ""))
        else:
            is_all_day = True
            start_time = str(start.get("date", ""))
            end_time = str(end.get("date", ""))

        summary = item.get("summary") if item.get("summary") is not None else "(No title)"
        location = item.get("location")
        html_link = item.get("htmlLink")
        event_id = str(item.get("id", ""))

        parsed_events.append(
            CalendarEventItem(
                id=event_id,
                summary=summary,
                start_time=start_time,
                end_time=end_time,
                is_all_day=is_all_day,
                location=location,
                html_link=html_link,
            )
        )

    # Sort all-day events first, followed chronologically by start_time
    parsed_events.sort(key=lambda ev: (0 if ev.is_all_day else 1, ev.start_time))

    return CalendarAgendaResult(
        events=parsed_events,
        total_count=len(parsed_events),
        date_str=today_str,
        is_configured=True,
        error=None,
    )


async def fetch_today_agenda(
    config: CalendarConfig | None = None,
    service: Any = None,
    target_date: date | None = None,
    allow_interactive: bool | None = None,
) -> CalendarAgendaResult:
    """Asynchronously fetch today's agenda from Google Calendar.

    Args:
        config: Optional CalendarConfig instance.
        service: Optional injected Google Calendar Resource service for testing.
        target_date: Optional target date to fetch agenda for (defaults to today).
        allow_interactive: Optional flag controlling interactive OAuth authorization flow.

    Returns:
        CalendarAgendaResult containing parsed events or graceful unconfigured/error state.
    """
    return await asyncio.to_thread(_fetch_agenda_sync, config, service, target_date, allow_interactive)
