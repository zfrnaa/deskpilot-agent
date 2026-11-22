"""Local screenshot organization and semantic renaming logic."""

from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from deskpilot.agent_tasks.screenshot_agent.state import ScreenshotItem

logger = logging.getLogger(__name__)

DEFAULT_CATEGORY_FOLDERS = {
    "WORK_NOTES": "Work Notes",
    "DUE_DILIGENCE": "Due Diligence",
    "BRAINSTORM": "Brainstorm",
    "NOTION_NOTE": "Work Notes",
    "LOCAL_KEEP": "General",
}


def slugify_description(text: str, max_words: int = 5) -> str:
    """Convert a title or short description into a clean hyphenated slug."""
    if not text:
        return "screenshot"
    # Remove non-alphanumeric chars except whitespace and hyphens
    cleaned = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    words = cleaned.split()
    if not words:
        return "screenshot"
    selected = words[:max_words]
    slug = "-".join(selected)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "screenshot"


def generate_semantic_filename(item: ScreenshotItem) -> str:
    """Generate a clean semantic filename adhering to YYYY-MM-DD_<short-description>.<ext>."""
    # Obtain date prefix from file mtime or creation time, fallback to today
    date_str = datetime.now().strftime("%Y-%m-%d")
    try:
        if item.path.exists():
            stat = item.path.stat()
            mtime_dt = datetime.fromtimestamp(stat.st_mtime)
            date_str = mtime_dt.strftime("%Y-%m-%d")
    except Exception:
        pass

    slug = slugify_description(item.title or item.cluster_tag)
    ext = item.path.suffix.lower() if item.path and item.path.suffix else ".png"
    return f"{date_str}_{slug}{ext}"


def plan_local_organization(
    items: list[ScreenshotItem],
    screenshots_dir: Path,
    category_folders: dict[str, str] | None = None,
    delete_synced_local: bool = True,
) -> list[ScreenshotItem]:
    """Calculate target destination paths for items that remain locally."""
    mapping = category_folders or DEFAULT_CATEGORY_FOLDERS
    base_dir = Path(screenshots_dir).expanduser().resolve()

    used_paths: set[Path] = set()

    for item in items:
        # If the file was deleted locally after syncing, skip it
        if getattr(item, "deleted_locally", False):
            item.target_path = None
            continue

        # If item was synced (including image) and delete_synced_local is True, it will be unlinked, so skip
        if getattr(item, "is_synced", False) and getattr(item, "image_synced", False) and delete_synced_local:
            item.target_path = None
            continue

        folder_name = mapping.get(item.classification, "General")
        subfolder = (base_dir / folder_name).resolve()
        if not subfolder.is_relative_to(base_dir):
            logger.warning(
                "Skipping organization for %s: category folder '%s' escapes screenshots_dir",
                item.filename,
                folder_name,
            )
            item.target_path = None
            continue

        candidate_name = generate_semantic_filename(item)
        target = subfolder / candidate_name

        # Handle duplicate filenames in same destination folder
        counter = 1
        stem = target.stem
        ext = target.suffix
        while target in used_paths or (target.exists() and target != item.path.resolve()):
            target = subfolder / f"{stem}_{counter}{ext}"
            counter += 1

        used_paths.add(target)
        item.target_path = target

    return items


def execute_local_organization(
    items: list[ScreenshotItem],
) -> tuple[int, list[str]]:
    """Execute planned moves and renames for eligible screenshot items.

    Ensures target parent directories exist, and moves files safely.
    """
    organized_count = 0
    errors: list[str] = []

    for item in items:
        target = getattr(item, "target_path", None)
        if not target or getattr(item, "deleted_locally", False):
            continue

        src = item.path.resolve()
        dst = target.resolve()

        if src == dst:
            item.organized_locally = True
            continue

        try:
            if not src.exists():
                logger.warning("Source screenshot does not exist for organizing: %s", src)
                continue

            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            item.path = dst
            item.filename = dst.name
            item.organized_locally = True
            organized_count += 1
        except Exception as e:
            msg = f"Failed to move {src.name} to {dst}: {e}"
            logger.error(msg)
            errors.append(msg)

    return organized_count, errors
