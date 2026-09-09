"""State definitions and models for the Downloads Hygiene Agent."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import TypedDict

from pydantic import BaseModel, model_validator


class DownloadFileCategory(str, Enum):
    """Categories for files discovered in the Downloads directory."""

    INSTALLER = "installer"          # .exe, .msi, .pkg, .deb
    ARCHIVE = "archive"              # .zip, .tar, .gz, .7z, .rar
    DOCUMENT = "document"            # .pdf, .docx, .xlsx, .pptx, .csv
    MEDIA = "media"                  # .mp4, .mkv, .mp3, .png, .jpg
    DUPLICATE = "duplicate"          # e.g. "document (1).pdf"
    OTHER = "other"


class ProposedAction(str, Enum):
    """Hygiene action proposed for a download item."""

    DELETE = "delete"                # Safe to delete (e.g. old installers > N days, duplicates)
    ARCHIVE = "archive"              # Move to Downloads/Archive
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
    archive_dir: Path
    installer_max_age_days: int
    items: list[DownloadItem]
    action_plan: dict[str, list[DownloadItem]]
    approved_actions: list[str]  # e.g. ["delete_installers", "archive_documents"]
    total_bytes_freed: int
    total_files_moved: int
    errors: list[str]
    auto_approve: bool


def create_initial_state(
    downloads_dir: Path,
    archive_dir: Path | None = None,
    installer_max_age_days: int = 30,
    auto_approve: bool = False,
) -> DownloadsAgentState:
    """Create a fully initialized DownloadsAgentState dictionary."""
    resolved_downloads = Path(downloads_dir).expanduser().resolve()
    resolved_archive = (
        Path(archive_dir).expanduser().resolve()
        if archive_dir is not None
        else resolved_downloads / "Archive"
    )
    return {
        "downloads_dir": resolved_downloads,
        "archive_dir": resolved_archive,
        "installer_max_age_days": installer_max_age_days,
        "items": [],
        "action_plan": {},
        "approved_actions": [],
        "total_bytes_freed": 0,
        "total_files_moved": 0,
        "errors": [],
        "auto_approve": auto_approve,
    }
