"""Fast non-LLM boot tasks for DeskPilot."""

from deskpilot.boot_tasks.bookmarks import BookmarkAuditResult, audit_floorp_bookmarks
from deskpilot.boot_tasks.calendar_briefing import (
    CalendarAgendaResult,
    CalendarEventItem,
    fetch_today_agenda,
    run_calendar_oauth_flow,
)
from deskpilot.boot_tasks.package_checker import (
    WingetUpdateItem,
    WingetUpdateResult,
    check_winget_updates,
)
from deskpilot.boot_tasks.system_hygiene import TempCleanResult, clean_temp_directory

__all__ = [
    "BookmarkAuditResult",
    "CalendarAgendaResult",
    "CalendarEventItem",
    "TempCleanResult",
    "WingetUpdateItem",
    "WingetUpdateResult",
    "audit_floorp_bookmarks",
    "clean_temp_directory",
    "check_winget_updates",
    "fetch_today_agenda",
    "run_calendar_oauth_flow",
]

