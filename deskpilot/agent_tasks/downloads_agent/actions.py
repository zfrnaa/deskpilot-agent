"""Core analysis, categorization, and execution actions for Downloads Hygiene Agent."""

from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

from deskpilot.agent_tasks.downloads_agent.state import (
    DownloadFileCategory,
    DownloadItem,
    ProposedAction,
)

# Regex matching downloaded duplicates like "filename (1).ext", "setup (2).exe", "archive (10).zip"
DUPLICATE_PATTERN = re.compile(
    r"^.+\s\(\d+\)\.[a-zA-Z0-9]+(?:\.[a-zA-Z0-9]+)?$",
    re.IGNORECASE,
)

INSTALLER_EXTENSIONS = {".exe", ".msi", ".pkg", ".deb", ".dmg", ".appimage", ".iso"}
ARCHIVE_EXTENSIONS = {
    ".zip",
    ".tar",
    ".gz",
    ".7z",
    ".rar",
    ".tgz",
    ".bz2",
    ".xz",
    ".tar.gz",
    ".tar.bz2",
    ".tar.xz",
}
DOCUMENT_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".xlsx",
    ".pptx",
    ".csv",
    ".txt",
    ".doc",
    ".xls",
    ".ppt",
    ".rtf",
    ".odt",
}
MEDIA_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".mp3",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".wav",
    ".webm",
    ".avi",
    ".mov",
    ".flac",
}


def format_bytes(num_bytes: int) -> str:
    """Format bytes into human-readable representation."""
    val = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(val) < 1024.0:
            return f"{val:.1f} {unit}" if unit != "B" else f"{int(val)} B"
        val /= 1024.0
    return f"{val:.1f} PB"


def scan_directory(downloads_dir: Path) -> tuple[list[DownloadItem], list[str]]:
    """Scan downloads directory for top-level files, calculating file sizes and ages."""
    errors: list[str] = []
    path = Path(downloads_dir).expanduser().resolve()

    if not path.exists() or not path.is_dir():
        return [], [f"Downloads directory does not exist: {path}"]

    items: list[DownloadItem] = []
    now = time.time()

    try:
        for entry in path.iterdir():
            # Skip subdirectories (such as Archive) - only triage top-level files
            if entry.is_file():
                try:
                    stat = entry.stat()
                    age_seconds = max(0.0, now - stat.st_mtime)
                    age_days = age_seconds / 86400.0
                    items.append(
                        DownloadItem(
                            path=entry,
                            filename=entry.name,
                            size_bytes=stat.st_size,
                            category=DownloadFileCategory.OTHER,
                            age_days=age_days,
                        )
                    )
                except Exception as file_err:
                    errors.append(f"Failed reading file stats for {entry.name}: {file_err}")
    except Exception as dir_err:
        errors.append(f"Error reading directory {path}: {dir_err}")

    # Deterministic sorting by filename
    items.sort(key=lambda i: i.filename.lower())
    return items, errors


def get_item_action_key(item: DownloadItem) -> str:
    """Determine the action plan category key for an item."""
    if item.proposed_action == ProposedAction.DELETE:
        if item.category == DownloadFileCategory.INSTALLER:
            return "delete_installers"
        elif item.category == DownloadFileCategory.DUPLICATE:
            return "delete_duplicates"
        else:
            return f"delete_{item.category.value}s"
    elif item.proposed_action == ProposedAction.ARCHIVE:
        if item.category == DownloadFileCategory.DOCUMENT:
            return "archive_documents"
        elif item.category == DownloadFileCategory.ARCHIVE:
            return "archive_archives"
        else:
            return f"archive_{item.category.value}s"
    return "keep"


def categorize_item(
    item: DownloadItem,
    installer_max_age_days: int = 30,
) -> DownloadItem:
    """Classify a DownloadItem and propose a hygiene action based on rules."""
    # 1. Check duplicate naming convention first
    if DUPLICATE_PATTERN.match(item.filename):
        item.category = DownloadFileCategory.DUPLICATE
        item.proposed_action = ProposedAction.DELETE
        item.rationale = f"Duplicate download pattern: '{item.filename}'"
        return item

    # 2. Match extensions
    lower_name = item.filename.lower()
    suffix = ".tar.gz" if lower_name.endswith(".tar.gz") else item.path.suffix.lower()

    if suffix in INSTALLER_EXTENSIONS:
        item.category = DownloadFileCategory.INSTALLER
        if item.age_days > installer_max_age_days:
            item.proposed_action = ProposedAction.DELETE
            item.rationale = (
                f"Stale installer ({item.age_days:.1f} days old > {installer_max_age_days} days limit)"
            )
        else:
            item.proposed_action = ProposedAction.KEEP
            item.rationale = (
                f"Recent installer ({item.age_days:.1f} days old <= {installer_max_age_days} days limit)"
            )
    elif suffix in ARCHIVE_EXTENSIONS:
        item.category = DownloadFileCategory.ARCHIVE
        item.proposed_action = ProposedAction.ARCHIVE
        item.rationale = "Archive file ready to be organized into Archive directory"
    elif suffix in DOCUMENT_EXTENSIONS:
        item.category = DownloadFileCategory.DOCUMENT
        item.proposed_action = ProposedAction.ARCHIVE
        item.rationale = "Document ready to be organized into Archive directory"
    elif suffix in MEDIA_EXTENSIONS:
        item.category = DownloadFileCategory.MEDIA
        item.proposed_action = ProposedAction.KEEP
        item.rationale = "Media file kept in place"
    else:
        item.category = DownloadFileCategory.OTHER
        item.proposed_action = ProposedAction.KEEP
        item.rationale = "Unrecognized file type kept in place"

    return item


def categorize_items(
    items: list[DownloadItem],
    installer_max_age_days: int = 30,
) -> list[DownloadItem]:
    """Classify a list of DownloadItems and propose hygiene actions."""
    return [categorize_item(item, installer_max_age_days=installer_max_age_days) for item in items]


def assemble_action_plan(items: list[DownloadItem]) -> dict[str, list[DownloadItem]]:
    """Assemble actionable proposals grouped by action category key."""
    plan: dict[str, list[DownloadItem]] = {}
    for item in items:
        if item.proposed_action == ProposedAction.KEEP:
            continue
        key = get_item_action_key(item)
        plan.setdefault(key, []).append(item)
    return plan


def format_plan_summary(action_plan: dict[str, list[DownloadItem]]) -> list[str]:
    """Generate concise summary strings for each proposed action category."""
    summaries: list[str] = []
    labels = {
        "delete_installers": ("Safe to delete", "stale installer"),
        "delete_duplicates": ("Safe to delete", "duplicate download"),
        "archive_documents": ("Ready to archive", "document"),
        "archive_archives": ("Ready to archive", "archive"),
    }

    for key, group in action_plan.items():
        count = len(group)
        if count == 0:
            continue
        total_bytes = sum(i.size_bytes for i in group)
        bytes_str = format_bytes(total_bytes)
        verb, noun = labels.get(key, ("Action proposed", key))
        plural_noun = f"{noun}s" if count != 1 else noun
        summaries.append(f"{verb}: {count} {plural_noun} ({bytes_str})")

    return summaries


def _resolve_safe_destination(dest_dir: Path, original_name: str) -> Path:
    """Find a non-colliding destination path by appending a numeric suffix if needed."""
    candidate = dest_dir / original_name
    if not candidate.exists():
        return candidate

    orig_path = Path(original_name)
    stem = orig_path.stem
    suffix = orig_path.suffix

    counter = 1
    while True:
        candidate = dest_dir / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def execute_approved_actions(
    items: list[DownloadItem],
    approved_actions: list[str],
    archive_dir: Path,
) -> tuple[int, int, list[str]]:
    """Execute deletions and archive moves strictly for approved action groups.

    Safety Guarantee:
    Files are ONLY modified if their action category is present in approved_actions.
    Returns (total_bytes_freed, total_files_moved, errors).
    """
    total_freed = 0
    total_moved = 0
    errors: list[str] = []

    if not approved_actions:
        return total_freed, total_moved, errors

    approved_set = set(approved_actions)
    resolved_archive = Path(archive_dir).expanduser().resolve()

    for item in items:
        action_key = get_item_action_key(item)
        if action_key not in approved_set or item.is_executed:
            continue

        if item.proposed_action == ProposedAction.DELETE:
            try:
                if item.path.exists() and item.path.is_file():
                    freed = item.size_bytes if item.size_bytes > 0 else item.path.stat().st_size
                    item.path.unlink()
                    item.is_executed = True
                    total_freed += freed
                else:
                    errors.append(f"File not found for deletion: {item.path}")
            except Exception as e:
                errors.append(f"Failed to delete {item.path}: {e}")

        elif item.proposed_action == ProposedAction.ARCHIVE:
            try:
                if item.path.exists() and item.path.is_file():
                    resolved_archive.mkdir(parents=True, exist_ok=True)
                    dest = _resolve_safe_destination(resolved_archive, item.path.name)
                    shutil.move(str(item.path), str(dest))
                    item.path = dest
                    item.is_executed = True
                    total_moved += 1
                else:
                    errors.append(f"File not found for archiving: {item.path}")
            except Exception as e:
                errors.append(f"Failed to move {item.path} to {resolved_archive}: {e}")

    return total_freed, total_moved, errors
