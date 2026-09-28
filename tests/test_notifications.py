from __future__ import annotations

from unittest.mock import patch

from deskpilot.boot_tasks import (
    CalendarAgendaResult,
    CalendarEventItem,
    TempCleanResult,
    WingetUpdateItem,
    WingetUpdateResult,
)
from deskpilot.state import BootState
from deskpilot.ui.notifications import format_boot_notification, send_windows_toast


def test_format_boot_notification_populated_state():
    state = BootState()
    state.system_hygiene = TempCleanResult(bytes_freed=15728640, files_removed=42, dirs_removed=5)
    state.winget_updates = WingetUpdateResult(
        total_count=2,
        updates=[
            WingetUpdateItem(name="Git", id="Git.Git", version="2.43.0", available_version="2.44.0"),
            WingetUpdateItem(name="Neovim", id="Neovim.Neovim", version="0.9.5", available_version="0.10.0"),
        ],
    )
    state.calendar_agenda = CalendarAgendaResult(
        events=[CalendarEventItem(id="ev1", summary="Daily Standup", start_time="09:00", end_time="09:30")],
        total_count=1,
        is_configured=True,
    )

    title, body = format_boot_notification(state)
    assert "DeskPilot" in title
    assert "15.0 MB" in body or "15.0 MB cleaned" in body
    assert "2 package update(s) available" in body
    assert "Daily Standup" in body


def test_format_boot_notification_empty_state():
    state = BootState()
    title, body = format_boot_notification(state)
    assert "DeskPilot" in title
    assert "Boot sequence completed" in body


def test_send_windows_toast_invokes_powershell():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        success = send_windows_toast("Test Title", "Test Message", action_command="notepad.exe")
        assert success is True
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "powershell" in args[0].lower()
