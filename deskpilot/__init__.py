"""DeskPilot: Personal multi-agent boot orchestrator."""

from deskpilot.config import (
    CalendarConfig,
    DownloadsConfig,
    FloorpConfig,
    NotionConfig,
    ScreenshotsConfig,
    Settings,
    TempCleanerConfig,
    WingetConfig,
    load_settings,
)
from deskpilot.state import BootState

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "CalendarConfig",
    "DownloadsConfig",
    "FloorpConfig",
    "NotionConfig",
    "ScreenshotsConfig",
    "Settings",
    "TempCleanerConfig",
    "WingetConfig",
    "load_settings",
    "BootState",
]