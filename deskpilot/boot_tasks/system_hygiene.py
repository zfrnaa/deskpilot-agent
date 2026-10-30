"""Windows %TEMP% cleaner boot task with safe locked-file skip, memory dump and AI hygiene."""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
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

    def merge(self, other: TempCleanResult) -> None:
        """Accumulate results from another cleanup step."""
        self.bytes_freed += other.bytes_freed
        self.files_removed += other.files_removed
        self.dirs_removed += other.dirs_removed
        self.errors.extend(other.errors)


def is_file_locked(path: Path) -> bool:
    """Check if a file is currently locked or in use by another process.

    On Windows, attempting to rename an open file to its existing path raises
    PermissionError if any process holds a handle without delete sharing.
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


def _clean_directory_tree_sync(
    dir_path: Path,
    max_age_hours: int,
    dry_run: bool,
    delete_empty_dirs: bool = True,
) -> TempCleanResult:
    """Synchronously clean unlocked files older than max_age_hours in dir_path."""
    if not dir_path.exists():
        return TempCleanResult(
            errors=[f"Directory does not exist: {dir_path}"]
        )
    if not dir_path.is_dir():
        return TempCleanResult(
            errors=[f"Path is not a directory: {dir_path}"]
        )

    cutoff_time = time.time() - (max_age_hours * 3600)
    bytes_freed = 0
    files_removed = 0
    dirs_removed = 0
    errors: list[str] = []
    would_remove_files: set[Path] = set()
    would_remove_dirs: set[Path] = set()

    for root_str, _, files in os.walk(dir_path, topdown=False):
        root_dir = Path(root_str)

        # 1. Clean eligible files in current directory
        for file_name in files:
            file_path = root_dir / file_name
            try:
                stat_result = file_path.lstat()
            except OSError as e:
                errors.append(f"Failed to stat {file_path}: {e}")
                continue

            if stat_result.st_mtime > cutoff_time:
                continue

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
                    try:
                        os.chmod(file_path, stat.S_IWRITE)
                        file_path.unlink()
                        files_removed += 1
                        bytes_freed += file_size
                    except (PermissionError, OSError):
                        pass
                except OSError as e:
                    errors.append(f"Failed to delete {file_path}: {e}")

        # 2. Clean empty directories if enabled
        if delete_empty_dirs and root_dir != dir_path:
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
                    pass

    return TempCleanResult(
        bytes_freed=bytes_freed,
        files_removed=files_removed,
        dirs_removed=dirs_removed,
        errors=errors,
    )


def _clean_memory_dumps_sync(dump_max_age_days: int, dry_run: bool) -> TempCleanResult:
    """Clean crash dumps (.dmp) and memory dumps older than specified days."""
    result = TempCleanResult()
    cutoff_time = time.time() - (dump_max_age_days * 86400)

    dump_locations: list[Path] = []
    local_appdata = os.getenv("LOCALAPPDATA")
    if local_appdata:
        dump_locations.append(Path(local_appdata) / "CrashDumps")

    system_root = os.getenv("SystemRoot", r"C:\Windows")
    dump_locations.append(Path(system_root) / "Minidump")
    mem_dmp = Path(system_root) / "MEMORY.DMP"

    if mem_dmp.exists() and mem_dmp.is_file():
        try:
            st = mem_dmp.lstat()
            if st.st_mtime <= cutoff_time and not is_file_locked(mem_dmp):
                if dry_run:
                    result.files_removed += 1
                    result.bytes_freed += st.st_size
                else:
                    mem_dmp.unlink()
                    result.files_removed += 1
                    result.bytes_freed += st.st_size
        except (PermissionError, OSError) as e:
            result.errors.append(f"Failed to clean {mem_dmp}: {e}")

    for loc in dump_locations:
        if not loc.exists() or not loc.is_dir():
            continue
        sub_res = _clean_directory_tree_sync(
            loc,
            max_age_hours=dump_max_age_days * 24,
            dry_run=dry_run,
            delete_empty_dirs=False,
        )
        result.merge(sub_res)

    return result


def _clean_antigravity_orphans_sync(
    antigravity_dir: Path | None = None,
    dry_run: bool = False,
) -> TempCleanResult:
    """Clean orphaned conversation brains and db files no longer registered in conversation_summaries.db."""
    result = TempCleanResult()
    base_dir = antigravity_dir or (Path.home() / ".gemini" / "antigravity")

    if not base_dir.exists():
        return result

    db_path = base_dir / "conversation_summaries.db"
    if not db_path.exists():
        return result

    try:
        conn = sqlite3.connect(db_path)
        active_ids = {
            row[0]
            for row in conn.cursor()
            .execute("SELECT conversation_id FROM conversation_summaries")
            .fetchall()
        }
        conn.close()
    except Exception as e:
        result.errors.append(f"Failed to query conversation_summaries.db: {e}")
        return result

    brain_dir = base_dir / "brain"
    if brain_dir.exists() and brain_dir.is_dir():
        for item in brain_dir.iterdir():
            if item.is_dir() and item.name not in active_ids:
                dir_bytes = 0
                dir_files = 0
                try:
                    for root, _, files in os.walk(item):
                        for f in files:
                            fp = Path(root) / f
                            try:
                                dir_bytes += fp.stat().st_size
                                dir_files += 1
                            except OSError:
                                pass
                    if dry_run:
                        result.bytes_freed += dir_bytes
                        result.files_removed += dir_files
                        result.dirs_removed += 1
                    else:
                        shutil.rmtree(item, ignore_errors=True)
                        result.bytes_freed += dir_bytes
                        result.files_removed += dir_files
                        result.dirs_removed += 1
                except Exception as err:
                    result.errors.append(f"Failed to prune orphaned brain {item.name}: {err}")

    conv_dir = base_dir / "conversations"
    if conv_dir.exists() and conv_dir.is_dir():
        for db_file in conv_dir.glob("*.db"):
            if db_file.stem not in active_ids:
                try:
                    size = db_file.stat().st_size
                    if dry_run:
                        result.bytes_freed += size
                        result.files_removed += 1
                    else:
                        db_file.unlink()
                        result.bytes_freed += size
                        result.files_removed += 1
                except Exception as err:
                    result.errors.append(f"Failed to prune orphaned conv db {db_file.name}: {err}")

    crashes_dir = base_dir / "crashes"
    if crashes_dir.exists() and crashes_dir.is_dir():
        crash_res = _clean_directory_tree_sync(
            crashes_dir,
            max_age_hours=24,
            dry_run=dry_run,
            delete_empty_dirs=False,
        )
        result.merge(crash_res)

    return result


async def clean_temp_directory(
    temp_path: Path | None = None,
    max_age_hours: int = 24,
    dry_run: bool = False,
    config: TempCleanerConfig | None = None,
) -> TempCleanResult:
    """Safely clean unlocked files and empty directories in temporary and cache locations."""
    if config is not None and not config.enabled:
        return TempCleanResult()

    result = TempCleanResult()

    if config is not None:
        primary_temp = temp_path or config.get_resolved_temp_path()
        clean_win_temp = config.clean_windows_temp
        clean_dumps = config.clean_memory_dumps
        clean_ag = config.clean_antigravity_orphans
        dump_days = config.dump_max_age_days
        if max_age_hours == 24 and config.max_age_hours != 24:
            max_age_hours = config.max_age_hours
    else:
        primary_temp = temp_path or Path(tempfile.gettempdir()).resolve()
        clean_win_temp = False
        clean_dumps = False
        clean_ag = False
        dump_days = 7

    user_temp_res = await asyncio.to_thread(
        _clean_directory_tree_sync,
        primary_temp,
        max_age_hours,
        dry_run,
    )
    result.merge(user_temp_res)

    if clean_win_temp:
        sys_temp = Path(os.getenv("SystemRoot", r"C:\Windows")) / "Temp"
        if sys_temp.exists() and sys_temp != primary_temp:
            win_temp_res = await asyncio.to_thread(
                _clean_directory_tree_sync,
                sys_temp,
                max_age_hours,
                dry_run,
            )
            result.merge(win_temp_res)

    if clean_dumps:
        dump_res = await asyncio.to_thread(_clean_memory_dumps_sync, dump_days, dry_run)
        result.merge(dump_res)

    if clean_ag:
        ag_res = await asyncio.to_thread(_clean_antigravity_orphans_sync, None, dry_run)
        result.merge(ag_res)

    return result
