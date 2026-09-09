from __future__ import annotations

import os
import stat
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from deskpilot.boot_tasks import TempCleanResult, clean_temp_directory
from deskpilot.config import TempCleanerConfig


def _create_file_with_age(path: Path, content: bytes | str, age_hours: float) -> Path:
    """Helper to create a file and set its modification and access time to age_hours in the past."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_bytes(content)
    past_time = time.time() - (age_hours * 3600)
    os.utime(path, (past_time, past_time))
    return path


def test_temp_clean_result_model():
    """Verify TempCleanResult model instantiation and default values."""
    res_default = TempCleanResult()
    assert res_default.bytes_freed == 0
    assert res_default.files_removed == 0
    assert res_default.dirs_removed == 0
    assert res_default.errors == []

    res_custom = TempCleanResult(
        bytes_freed=1048576,
        files_removed=15,
        dirs_removed=3,
        errors=["mock error"],
    )
    assert res_custom.bytes_freed == 1048576
    assert res_custom.files_removed == 15
    assert res_custom.dirs_removed == 3
    assert res_custom.errors == ["mock error"]


@pytest.mark.asyncio
async def test_age_filtering_deletes_old_keeps_new(tmp_path: Path):
    """Verify files older than max_age_hours are deleted while newer files are kept."""
    old_file = _create_file_with_age(tmp_path / "stale.tmp", "old content", age_hours=36)
    new_file = _create_file_with_age(tmp_path / "active.tmp", "new content", age_hours=2)
    old_size = old_file.stat().st_size

    result = await clean_temp_directory(temp_path=tmp_path, max_age_hours=24)

    assert not old_file.exists()
    assert new_file.exists()
    assert result.files_removed == 1
    assert result.bytes_freed == old_size
    assert result.errors == []


@pytest.mark.asyncio
async def test_directory_cleanup_removes_empty_subdirs_keeps_populated(tmp_path: Path):
    """Verify empty subdirectories are removed bottom-up, while subdirectories with kept files remain."""
    # Already empty subdirectory
    empty_sub = tmp_path / "empty_dir"
    empty_sub.mkdir()

    # Subdirectory with only old files (should be removed once files are deleted)
    dir_with_old = tmp_path / "dir_old"
    _create_file_with_age(dir_with_old / "old1.tmp", "data", age_hours=48)

    # Subdirectory with newer file (should NOT be removed)
    dir_with_new = tmp_path / "dir_new"
    _create_file_with_age(dir_with_new / "active.tmp", "keep me", age_hours=1)

    # Nested subdirectories where all descendants are old
    nested_child = tmp_path / "parent_dir" / "child_dir"
    _create_file_with_age(nested_child / "deep_old.tmp", "nested data", age_hours=30)

    result = await clean_temp_directory(temp_path=tmp_path, max_age_hours=24)

    # Root temp directory must never be removed
    assert tmp_path.exists()

    # Empty subdirectories and those whose old files were deleted should be removed
    assert not empty_sub.exists()
    assert not dir_with_old.exists()
    assert not (tmp_path / "parent_dir").exists()
    assert not nested_child.exists()

    # Directory with active file must remain
    assert dir_with_new.exists()
    assert (dir_with_new / "active.tmp").exists()

    # Removed dirs: empty_sub, dir_old, parent_dir/child_dir, parent_dir = 4
    assert result.dirs_removed == 4
    assert result.files_removed == 2


@pytest.mark.asyncio
async def test_locked_file_tolerance_real_handle(tmp_path: Path):
    """Verify that an open/locked file is skipped gracefully without crashing or stopping other deletions."""
    locked_file = _create_file_with_age(tmp_path / "locked.tmp", "locked file content", age_hours=48)
    unlocked_file = _create_file_with_age(tmp_path / "unlocked.tmp", "unlocked file content", age_hours=48)
    unlocked_size = unlocked_file.stat().st_size

    # Hold locked_file open with an active file handle
    with open(locked_file, "r+b"):
        result = await clean_temp_directory(temp_path=tmp_path, max_age_hours=24)

        # Locked file must remain untouched
        assert locked_file.exists()
        # Unlocked file must be cleaned
        assert not unlocked_file.exists()
        assert result.files_removed == 1
        assert result.bytes_freed == unlocked_size


@pytest.mark.asyncio
async def test_locked_file_tolerance_simulated_permission_error(tmp_path: Path):
    """Verify permission errors during unlinking are caught and handled gracefully."""
    stale_file1 = _create_file_with_age(tmp_path / "f1.tmp", "content1", age_hours=30)
    stale_file2 = _create_file_with_age(tmp_path / "f2.tmp", "content2", age_hours=30)
    size2 = stale_file2.stat().st_size

    orig_unlink = Path.unlink

    def mock_unlink(self: Path, *args, **kwargs):
        if self.name == "f1.tmp":
            raise PermissionError("[WinError 32] The process cannot access the file because it is being used")
        return orig_unlink(self, *args, **kwargs)

    with patch.object(Path, "unlink", side_effect=mock_unlink, autospec=True):
        result = await clean_temp_directory(temp_path=tmp_path, max_age_hours=24)

    assert stale_file1.exists()
    assert not stale_file2.exists()
    assert result.files_removed == 1
    assert result.bytes_freed == size2


@pytest.mark.asyncio
async def test_byte_calculations(tmp_path: Path):
    """Verify exact calculation of freed bytes across multiple deleted files."""
    chunk1 = b"A" * 1024
    chunk2 = b"B" * 2048
    chunk3 = b"C" * 4096

    _create_file_with_age(tmp_path / "f1.bin", chunk1, age_hours=48)
    _create_file_with_age(tmp_path / "f2.bin", chunk2, age_hours=48)
    _create_file_with_age(tmp_path / "f3.bin", chunk3, age_hours=48)

    result = await clean_temp_directory(temp_path=tmp_path, max_age_hours=24)

    assert result.files_removed == 3
    assert result.bytes_freed == 1024 + 2048 + 4096


@pytest.mark.asyncio
async def test_dry_run_mode(tmp_path: Path):
    """Verify dry_run calculates what would be removed without modifying disk state."""
    old_file = _create_file_with_age(tmp_path / "subdir" / "old.tmp", "dry run test", age_hours=40)
    file_size = old_file.stat().st_size
    empty_dir = tmp_path / "empty_dir"
    empty_dir.mkdir()

    result = await clean_temp_directory(temp_path=tmp_path, max_age_hours=24, dry_run=True)

    # Statistics match what would be removed
    assert result.files_removed == 1
    assert result.bytes_freed == file_size
    assert result.dirs_removed == 2  # subdir and empty_dir

    # Filesystem must remain completely intact
    assert old_file.exists()
    assert (tmp_path / "subdir").exists()
    assert empty_dir.exists()


@pytest.mark.asyncio
async def test_clean_temp_with_config(tmp_path: Path):
    """Verify integration with TempCleanerConfig (enabled flag, custom path, max_age)."""
    target_file = _create_file_with_age(tmp_path / "custom.tmp", "config test", age_hours=15)

    # Config with enabled=False should bypass cleanup
    cfg_disabled = TempCleanerConfig(enabled=False, temp_path=tmp_path, max_age_hours=10)
    result_disabled = await clean_temp_directory(config=cfg_disabled)
    assert result_disabled.files_removed == 0
    assert target_file.exists()

    # Config with enabled=True and max_age_hours=10 should clean 15h-old file
    cfg_enabled = TempCleanerConfig(enabled=True, temp_path=tmp_path, max_age_hours=10)
    result_enabled = await clean_temp_directory(config=cfg_enabled)
    assert result_enabled.files_removed == 1
    assert not target_file.exists()


@pytest.mark.asyncio
async def test_non_existent_directory_handled_gracefully(tmp_path: Path):
    """Verify non-existent directory path does not raise uncaught exception and logs an error."""
    missing_path = tmp_path / "non_existent_subdir"
    result = await clean_temp_directory(temp_path=missing_path)

    assert result.files_removed == 0
    assert result.bytes_freed == 0
    assert result.dirs_removed == 0
    assert len(result.errors) > 0
    assert any("does not exist" in err.lower() for err in result.errors)


def test_no_utf8_bom_in_hygiene_files():
    """Verify no UTF-8 BOM exists in system hygiene source and test files."""
    project_root = Path(__file__).parent.parent
    py_files = [
        project_root / "deskpilot" / "boot_tasks" / "__init__.py",
        project_root / "deskpilot" / "boot_tasks" / "system_hygiene.py",
        project_root / "tests" / "test_system_hygiene.py",
    ]
    for py_file in py_files:
        if py_file.exists():
            raw_bytes = py_file.read_bytes()
            assert not raw_bytes.startswith(b"\xef\xbb\xbf"), f"BOM found in {py_file}"
