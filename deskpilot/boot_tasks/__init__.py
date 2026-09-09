"""Fast non-LLM boot tasks for DeskPilot."""

from deskpilot.boot_tasks.bookmarks import BookmarkAuditResult, audit_floorp_bookmarks
from deskpilot.boot_tasks.system_hygiene import TempCleanResult, clean_temp_directory

__all__ = [
    "BookmarkAuditResult",
    "TempCleanResult",
    "audit_floorp_bookmarks",
    "clean_temp_directory",
]

