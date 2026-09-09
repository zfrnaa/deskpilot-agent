"""Windows %TEMP% cleaner boot task with safe locked-file skip."""

from __future__ import annotations

import asyncio
import os
import stat
import tempfile
import time
from pathlib import Path

from pydantic import BaseModel, Field

from deskpilot.config import TempCleanerConfig


class TempCleanResult(BaseModel):
    """Execution statistics from temporary directory cleanup."""

    bytes_freed: int = 0
    files_removed: int = 0
    dirs_removed: int = 0
    errors: list[str] = Field(default_factory=list)


def is_file_locked(path: Path) -> bool:
    """Check if a file is currently locked or in use by another process.

    On Windows, attempting to rename an open file to its existing path raises
    PermissionError ([WinError 32]) if any process holds a handle without delete sharing.
    """
    if not path.is_file() and not path.is_symlink():
        return False
    try:
        os.rename(path, path)
        return False
    except PermissionError:
        return True
    except OSError:
        return True


def _clean_temp_directory_sync(
    temp_path: Path,
    max_age_hours: int,
    dry_run: bool,
) -> TempCleanResult:
    """Synchronous implementation of temp cleaner."""
    if not temp_path.exists():
        return TempCleanResult(
            errors=[f"Temp directory does not exist: {temp_path}"]
        )
    if not temp_path.is_dir():
        return TempCleanResult(
            errors=[f"Temp path is not a directory: {temp_path}"]
        )

    cutoff_time = time.time() - (max_age_hours * 3600)
    bytes_freed = 0
    files_removed = 0
    dirs_removed = 0
    errors: list[str] = []
    would_remove_files: set[Path] = set()
    would_remove_dirs: set[Path] = set()

    # Traverse bottom-up so empty child directories can be pruned cleanly
    for root_str, _, files in os.walk(temp_path, topdown=False):
        root_dir = Path(root_str)

        # 1. Clean eligible files in current directory
        for file_name in files:
            file_path = root_dir / file_name
            try:
                stat_result = file_path.lstat()
            except OSError as e:
                errors.append(f"Failed to stat {file_path}: {e}")
                continue

            # Skip files newer than max_age_hours
            if stat_result.st_mtime > cutoff_time:
                continue

            # Skip files locked by another process
            if is_file_locked(file_path):
                continue

            file_size = stat_result.st_size

            if dry_run:
                would_remove_files.add(file_path)
                files_removed += 1
                bytes_freed += file_size
            else:
                try:
                    file_path.unlink()
                    files_removed += 1
                    bytes_freed += file_size
                except PermissionError:
                    # Clear read-only attribute if present and retry unlink
                    try:
                        os.chmod(file_path, stat.S_IWRITE)
                        file_path.unlink()
                        files_removed += 1
                        bytes_freed += file_size
                    except (PermissionError, OSError):
                        # File is actively locked or inaccessible; skip gracefully
                        pass
                except OSError as e:
                    errors.append(f"Failed to delete {file_path}: {e}")

        # 2. Clean empty directories (never delete temp_path root itself)
        if root_dir != temp_path:
            if dry_run:
                try:
                    remaining = [
                        c
                        for c in root_dir.iterdir()
                        if c not in would_remove_files and c not in would_remove_dirs
                    ]
                    if not remaining:
                        would_remove_dirs.add(root_dir)
                        dirs_removed += 1
                except OSError as e:
                    errors.append(f"Failed to inspect directory {root_dir}: {e}")
            else:
                try:
                    if not any(root_dir.iterdir()):
                        root_dir.rmdir()
                        dirs_removed += 1
                except (PermissionError, OSError):
                    # Directory not empty or locked by running process; skip gracefully
                    pass

    return TempCleanResult(
        bytes_freed=bytes_freed,
        files_removed=files_removed,
        dirs_removed=dirs_removed,
        errors=errors,
    )


async def clean_temp_directory(
    temp_path: Path | None = None,
    max_age_hours: int = 24,
    dry_run: bool = False,
    config: TempCleanerConfig | None = None,
) -> TempCleanResult:
    """Safely clean unlocked files and empty directories in the temporary folder."""
    if config is not None:
        if not config.enabled:
            return TempCleanResult(
                bytes_freed=0,
                files_removed=0,
                dirs_removed=0,
                errors=[],
            )
        if temp_path is None:
            temp_path = config.get_resolved_temp_path()
        if max_age_hours == 24 and config.max_age_hours != 24:
            max_age_hours = config.max_age_hours

    if temp_path is None:
        temp_path = Path(tempfile.gettempdir()).resolve()
    else:
        temp_path = Path(temp_path).expanduser().resolve()

    return await asyncio.to_thread(
        _clean_temp_directory_sync,
        temp_path,
        max_age_hours,
        dry_run,
    )
