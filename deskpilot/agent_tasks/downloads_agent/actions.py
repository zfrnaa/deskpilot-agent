"""Core analysis, categorization, and execution actions for Downloads Hygiene Agent."""

from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

from deskpilot.agent_tasks.downloads_agent.state import (
    CATEGORY_FOLDERS,
    DownloadFileCategory,
    DownloadItem,
    ProposedAction,
)

# Regex matching downloaded duplicates like "filename (1).ext", "setup (2).exe", "archive (10).zip"
DUPLICATE_PATTERN = re.compile(
    r"^(.*)\s\(\d+\)(\.[a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)?)$",
    re.IGNORECASE,
)

# In-progress browser/download-manager files. Never touched. Mirrored in the Go daemon.
PARTIAL_DOWNLOAD_EXTENSIONS = {".crdownload", ".part", ".partial", ".download", ".opdownload", ".tmp"}

# Windows shell metadata files that control folder appearance; never moved.
WINDOWS_SHELL_FILES = {"desktop.ini", "thumbs.db"}

# System binaries are left in place rather than sorted.
SYSTEM_EXTENSIONS = {".dll", ".sys", ".drv"}

INSTALLER_EXTENSIONS = {
    ".exe", ".msi", ".msix", ".appx", ".pkg", ".deb", ".rpm", ".dmg", ".appimage", ".iso", ".apk",
}
ARCHIVE_EXTENSIONS = {
    ".zip", ".tar", ".gz", ".7z", ".rar", ".tgz", ".bz2", ".xz", ".tar.gz", ".tar.bz2", ".tar.xz",
}
DOCUMENT_EXTENSIONS = {
    ".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".txt", ".doc", ".xls", ".ppt", ".rtf", ".odt",
    ".ods", ".odp", ".md", ".epub",
}
IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico", ".tif", ".tiff", ".heic", ".psd",
}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".avi", ".mov", ".wmv", ".flv", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".opus"}
CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".html", ".css", ".go", ".java", ".c", ".cpp",
    ".h", ".cs", ".rs", ".sh", ".ps1", ".bat", ".sql", ".xml", ".yaml", ".yml", ".toml", ".ipynb",
}

_EXTENSION_CATEGORIES: list[tuple[set[str], DownloadFileCategory]] = [
    (ARCHIVE_EXTENSIONS, DownloadFileCategory.ARCHIVE),
    (DOCUMENT_EXTENSIONS, DownloadFileCategory.DOCUMENT),
    (IMAGE_EXTENSIONS, DownloadFileCategory.IMAGE),
    (VIDEO_EXTENSIONS, DownloadFileCategory.VIDEO),
    (AUDIO_EXTENSIONS, DownloadFileCategory.AUDIO),
    (CODE_EXTENSIONS, DownloadFileCategory.CODE),
]


def format_bytes(num_bytes: int) -> str:
    """Format bytes into human-readable representation."""
    val = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(val) < 1024.0:
            return f"{val:.1f} {unit}" if unit != "B" else f"{int(val)} B"
        val /= 1024.0
    return f"{val:.1f} PB"


_WIN_HIDDEN_OR_SYSTEM = 0x2 | 0x4  # FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM


def is_hidden_or_system(path: Path) -> bool:
    """True if Windows marks the file as hidden or system (no-op on other platforms)."""
    try:
        attrs = getattr(path.stat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attrs & _WIN_HIDDEN_OR_SYSTEM)


def is_ignored_download(name: str) -> bool:
    """True for hidden/temp files and in-progress partial downloads."""
    if name.startswith((".", "~")) or name.lower() in WINDOWS_SHELL_FILES:
        return True
    return Path(name).suffix.lower() in PARTIAL_DOWNLOAD_EXTENSIONS


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
            # Skip subdirectories (category folders) and symlinks - only triage regular top-level files
            if entry.is_symlink() or (hasattr(entry, "is_junction") and entry.is_junction()):
                continue
            if entry.is_file() and not is_ignored_download(entry.name) and not is_hidden_or_system(entry):
                try:
                    stat = entry.stat()
                    age_seconds = max(0.0, now - stat.st_mtime)
                    items.append(
                        DownloadItem(
                            path=entry,
                            filename=entry.name,
                            size_bytes=stat.st_size,
                            category=DownloadFileCategory.OTHER,
                            age_days=age_seconds / 86400.0,
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
        return f"delete_{item.category.value}s"
    elif item.proposed_action == ProposedAction.SORT:
        folder = CATEGORY_FOLDERS.get(item.category, "Other")
        return f"sort_{folder.lower()}"
    return "keep"


def _match_category(item: DownloadItem) -> DownloadFileCategory | None:
    lower_name = item.filename.lower()
    for compound in (".tar.gz", ".tar.bz2", ".tar.xz"):
        if lower_name.endswith(compound):
            return DownloadFileCategory.ARCHIVE
    suffix = item.path.suffix.lower()
    if suffix in INSTALLER_EXTENSIONS:
        return DownloadFileCategory.INSTALLER
    for extensions, category in _EXTENSION_CATEGORIES:
        if suffix in extensions:
            return category
    return None


def categorize_item(
    item: DownloadItem,
    installer_max_age_days: int = 30,
    min_file_age_minutes: int = 0,
) -> DownloadItem:
    """Classify a DownloadItem and propose a hygiene action based on rules."""
    # 0. Leave files that were just downloaded alone (may still be in use / being written)
    if item.age_days * 1440.0 < min_file_age_minutes:
        category = _match_category(item)
        item.category = category or DownloadFileCategory.OTHER
        item.proposed_action = ProposedAction.KEEP
        item.rationale = f"Recently downloaded (< {min_file_age_minutes} minutes old); left in place"
        return item

    # 1. Check duplicate naming convention first (guard: base file must exist in directory)
    dup_match = DUPLICATE_PATTERN.match(item.filename)
    if dup_match:
        base_filename = f"{dup_match.group(1)}{dup_match.group(2)}"
        base_path = item.path.parent / base_filename
        base_exists = base_path.exists()
        if not base_exists and item.path.parent.is_dir():
            base_lower = base_filename.lower()
            try:
                base_exists = any(entry.name.lower() == base_lower for entry in item.path.parent.iterdir())
            except OSError:
                pass
        if base_exists:
            item.category = DownloadFileCategory.DUPLICATE
            item.proposed_action = ProposedAction.DELETE
            item.rationale = f"Duplicate download pattern: '{item.filename}' (original '{base_filename}' exists)"
            return item

    # 2. System binaries stay put
    if item.path.suffix.lower() in SYSTEM_EXTENSIONS:
        item.category = DownloadFileCategory.OTHER
        item.proposed_action = ProposedAction.KEEP
        item.rationale = "System file kept in place"
        return item

    # 3. Match extensions
    category = _match_category(item)

    if category == DownloadFileCategory.INSTALLER and item.age_days > installer_max_age_days:
        item.category = category
        item.proposed_action = ProposedAction.DELETE
        item.rationale = f"Stale installer ({item.age_days:.1f} days old > {installer_max_age_days} days limit)"
        return item

    item.category = category or DownloadFileCategory.OTHER
    item.proposed_action = ProposedAction.SORT
    folder = CATEGORY_FOLDERS[item.category]
    if category == DownloadFileCategory.INSTALLER:
        item.rationale = (
            f"Recent installer ({item.age_days:.1f} days old <= {installer_max_age_days} days limit); "
            f"sort into {folder}"
        )
    elif category is None:
        item.rationale = f"Unrecognized file type; sort into {folder}"
    else:
        item.rationale = f"{item.category.value.capitalize()} file; sort into {folder}"
    return item


def categorize_items(
    items: list[DownloadItem],
    installer_max_age_days: int = 30,
    min_file_age_minutes: int = 0,
) -> list[DownloadItem]:
    """Classify a list of DownloadItems and propose hygiene actions."""
    return [
        categorize_item(item, installer_max_age_days=installer_max_age_days, min_file_age_minutes=min_file_age_minutes)
        for item in items
    ]


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
    }

    for key, group in action_plan.items():
        count = len(group)
        if count == 0:
            continue
        bytes_str = format_bytes(sum(i.size_bytes for i in group))
        if key.startswith("sort_"):
            folder = CATEGORY_FOLDERS.get(group[0].category, "Other")
            noun = "file" if count == 1 else "files"
            summaries.append(f"Sort into {folder}: {count} {noun} ({bytes_str})")
            continue
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
    sort_root: Path,
) -> tuple[int, int, list[str]]:
    """Execute deletions and category-folder moves strictly for approved action groups.

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
    resolved_root = Path(sort_root).expanduser().resolve()

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

        elif item.proposed_action == ProposedAction.SORT:
            dest_dir = resolved_root / CATEGORY_FOLDERS.get(item.category, "Other")
            try:
                if item.path.exists() and item.path.is_file():
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    dest = _resolve_safe_destination(dest_dir, item.path.name)
                    shutil.move(str(item.path), str(dest))
                    item.path = dest
                    item.is_executed = True
                    total_moved += 1
                else:
                    errors.append(f"File not found for sorting: {item.path}")
            except Exception as e:
                errors.append(f"Failed to move {item.path} to {dest_dir}: {e}")

    return total_freed, total_moved, errors
