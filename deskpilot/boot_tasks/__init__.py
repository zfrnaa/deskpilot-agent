"""Fast non-LLM boot tasks for DeskPilot."""

from deskpilot.boot_tasks.system_hygiene import TempCleanResult, clean_temp_directory

__all__ = [
    "TempCleanResult",
    "clean_temp_directory",
]
