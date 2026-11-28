"""State definitions and models for the Downloads Hygiene Agent."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import TypedDict

from pydantic import BaseModel, model_validator

# Files modified more recently than this are treated as still downloading / just downloaded
# and are left in place. Mirrored by the Go daemon (daemon/internal/watcher/downloads.go).
MIN_FILE_AGE_MINUTES = 10


class DownloadFileCategory(str, Enum):
    """Categories for files discovered in the Downloads directory."""

    INSTALLER = "installer"          # .exe, .msi, .pkg, .deb
    ARCHIVE = "archive"              # .zip, .tar, .gz, .7z, .rar
    DOCUMENT = "document"            # .pdf, .docx, .xlsx, .pptx, .csv
    IMAGE = "image"                  # .png, .jpg, .gif, .webp
    VIDEO = "video"                  # .mp4, .mkv, .mov
    AUDIO = "audio"                  # .mp3, .wav, .flac
    CODE = "code"                    # .py, .js, .json, .html
    DUPLICATE = "duplicate"          # e.g. "document (1).pdf"
    OTHER = "other"


# Destination subfolder (inside the Downloads directory) for each sortable category.
CATEGORY_FOLDERS: dict[DownloadFileCategory, str] = {
    DownloadFileCategory.INSTALLER: "Installers",
    DownloadFileCategory.ARCHIVE: "Archives",
    DownloadFileCategory.DOCUMENT: "Documents",
    DownloadFileCategory.IMAGE: "Images",
    DownloadFileCategory.VIDEO: "Videos",
    DownloadFileCategory.AUDIO: "Audio",
    DownloadFileCategory.CODE: "Code",
    DownloadFileCategory.OTHER: "Other",
}


class ProposedAction(str, Enum):
    """Hygiene action proposed for a download item."""

    DELETE = "delete"                # Safe to delete (e.g. old installers > N days, duplicates)
    SORT = "sort"                    # Move into Downloads/<Category> folder
    KEEP = "keep"                    # Keep in place


class DownloadItem(BaseModel):
    """Represents a single file discovered in Downloads during hygiene triage."""

    path: Path
    filename: str = ""
    size_bytes: int = 0
    category: DownloadFileCategory = DownloadFileCategory.OTHER
    age_days: float = 0.0
    proposed_action: ProposedAction = ProposedAction.KEEP
    rationale: str = ""
    is_executed: bool = False

    @model_validator(mode="after")
    def populate_filename(self) -> DownloadItem:
        """Ensure filename is populated from path.name if empty."""
        if not self.filename and self.path:
            self.filename = self.path.name
        return self


class DownloadsAgentState(TypedDict, total=False):
    """State schema for the Downloads Hygiene LangGraph workflow."""

    downloads_dir: Path
    sort_root: Path  # Parent directory for category folders (defaults to downloads_dir)
    installer_max_age_days: int
    min_file_age_minutes: int
    items: list[DownloadItem]
    action_plan: dict[str, list[DownloadItem]]
    approved_actions: list[str]  # e.g. ["delete_installers", "sort_images"]
    total_bytes_freed: int
    total_files_moved: int
    errors: list[str]
    auto_approve: bool


def create_initial_state(
    downloads_dir: Path,
    sort_root: Path | None = None,
    installer_max_age_days: int = 30,
    auto_approve: bool = False,
    min_file_age_minutes: int = MIN_FILE_AGE_MINUTES,
) -> DownloadsAgentState:
    """Create a fully initialized DownloadsAgentState dictionary."""
    resolved_downloads = Path(downloads_dir).expanduser().resolve()
    resolved_root = Path(sort_root).expanduser().resolve() if sort_root is not None else resolved_downloads
    return {
        "downloads_dir": resolved_downloads,
        "sort_root": resolved_root,
        "installer_max_age_days": installer_max_age_days,
        "min_file_age_minutes": min_file_age_minutes,
        "items": [],
        "action_plan": {},
        "approved_actions": [],
        "total_bytes_freed": 0,
        "total_files_moved": 0,
        "errors": [],
        "auto_approve": auto_approve,
    }
