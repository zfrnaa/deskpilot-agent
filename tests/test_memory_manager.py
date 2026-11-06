from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from deskpilot.boot_tasks.memory_manager import (
    MemoryStatus,
    MemoryCleanResult,
    detect_active_game,
    is_game_running,
    purge_standby_list,
    flush_modified_page_list,
    empty_system_working_set,
    perform_game_standby_purge,
    perform_periodic_memory_cleanup,
)


def test_memory_status_model():
    status = MemoryStatus(
        total_bytes=32 * 1024**3,
        available_bytes=16 * 1024**3,
        used_bytes=16 * 1024**3,
        memory_load_percent=50,
    )
    assert status.total_gb == 32.0
    assert status.available_gb == 16.0
    assert status.used_gb == 16.0
    assert status.memory_load_percent == 50


def test_detect_active_game():
    with patch("deskpilot.boot_tasks.memory_manager.get_running_process_names", return_value={"explorer.exe", "tslgame.exe", "discord.exe"}):
        active = detect_active_game()
        assert active == "tslgame.exe"
        assert is_game_running() is True

    with patch("deskpilot.boot_tasks.memory_manager.get_running_process_names", return_value={"explorer.exe", "chrome.exe"}):
        assert detect_active_game() is None
        assert is_game_running() is False


def test_purge_standby_list_mocked():
    with patch("ctypes.windll.ntdll.NtSetSystemInformation", return_value=0) as mock_nt, \
         patch("deskpilot.boot_tasks.memory_manager.enable_privilege", return_value=True):
        ok, err = purge_standby_list()
        assert ok is True
        assert err is None
        assert mock_nt.call_count == 1


def test_flush_modified_page_list_mocked():
    with patch("ctypes.windll.ntdll.NtSetSystemInformation", return_value=0) as mock_nt, \
         patch("deskpilot.boot_tasks.memory_manager.enable_privilege", return_value=True):
        ok, err = flush_modified_page_list()
        assert ok is True
        assert err is None
        assert mock_nt.call_count == 1


def test_perform_game_standby_purge():
    with patch("deskpilot.boot_tasks.memory_manager.detect_active_game", return_value="tslgame.exe"), \
         patch("deskpilot.boot_tasks.memory_manager.purge_standby_list", return_value=(True, None)):
        res = perform_game_standby_purge()
        assert res.game_running is True
        assert res.active_game == "tslgame.exe"
        assert res.standby_purged is True
        assert len(res.errors) == 0


def test_perform_periodic_memory_cleanup_skips_when_gaming():
    with patch("deskpilot.boot_tasks.memory_manager.detect_active_game", return_value="tslgame.exe"), \
         patch("deskpilot.boot_tasks.memory_manager.flush_modified_page_list") as mock_flush, \
         patch("deskpilot.boot_tasks.memory_manager.empty_system_working_set") as mock_sys, \
         patch("deskpilot.boot_tasks.memory_manager.trim_user_working_sets") as mock_trim, \
         patch("deskpilot.boot_tasks.memory_manager.purge_standby_list") as mock_standby:
        
        # When gaming, ALL cleanup (modified, system working set, user working sets, standby) must strictly skip!
        res = perform_periodic_memory_cleanup()
        assert res.game_running is True
        assert res.active_game == "tslgame.exe"
        assert "Skipped periodic working sets" in res.errors[0]
        assert mock_flush.call_count == 0
        assert mock_sys.call_count == 0
        assert mock_trim.call_count == 0
        assert mock_standby.call_count == 0


def test_perform_periodic_memory_cleanup_executes_when_not_gaming():
    with patch("deskpilot.boot_tasks.memory_manager.detect_active_game", return_value=None), \
         patch("deskpilot.boot_tasks.memory_manager.flush_modified_page_list", return_value=(True, None)), \
         patch("deskpilot.boot_tasks.memory_manager.empty_system_working_set", return_value=(True, None)), \
         patch("deskpilot.boot_tasks.memory_manager.trim_user_working_sets", return_value=(15, 5)), \
         patch("deskpilot.boot_tasks.memory_manager.purge_standby_list", return_value=(True, None)) as mock_standby:

        res = perform_periodic_memory_cleanup()
        assert res.game_running is False
        assert res.modified_flushed is True
        assert res.system_working_set_emptied is True
        assert res.processes_trimmed == 15
        assert res.processes_skipped == 5
        assert res.standby_purged is True
        assert mock_standby.call_count == 1
