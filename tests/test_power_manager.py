"""Tests for Windows power plan manager and game auto-detection."""

from __future__ import annotations

from subprocess import CompletedProcess
from unittest.mock import patch

from deskpilot.boot_tasks.power_manager import (
    get_active_power_plan_guid,
    resolve_power_plan_guid,
    set_power_plan,
)

SAMPLE_POWERCFG_LIST = """
Existing Power Schemes (* Active)
-----------------------------------
Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced) *
Power Scheme GUID: 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c  (High performance)
Power Scheme GUID: a1841308-3541-4fab-bc81-f71556f20b4a  (Power saver)
"""


def test_resolve_power_plan_guid_passthrough():
    guid = "8C5E7FDA-E8BF-4A96-9A85-A6E23A8C635C"
    assert resolve_power_plan_guid(guid) == guid.lower()


def test_resolve_power_plan_guid_by_name():
    with patch(
        "deskpilot.boot_tasks.power_manager._run_powercfg",
        return_value=CompletedProcess(["powercfg", "/list"], 0, SAMPLE_POWERCFG_LIST, ""),
    ):
        assert resolve_power_plan_guid("High performance") == "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
        assert resolve_power_plan_guid("NonExistent") is None


def test_get_active_power_plan_guid():
    with patch(
        "deskpilot.boot_tasks.power_manager._run_powercfg",
        return_value=CompletedProcess(["powercfg", "/list"], 0, SAMPLE_POWERCFG_LIST, ""),
    ):
        assert get_active_power_plan_guid() == "381b4222-f694-41f0-9685-ff5bb260df2e"


def test_set_power_plan_success_and_failure():
    with patch(
        "deskpilot.boot_tasks.power_manager._run_powercfg",
        return_value=CompletedProcess(["powercfg", "/setactive", "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"], 0, "", ""),
    ) as mock_run:
        ok, err = set_power_plan("8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c")
        assert ok is True
        assert err is None
        mock_run.assert_called_once_with(["/setactive", "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"])

    with patch(
        "deskpilot.boot_tasks.power_manager._run_powercfg",
        return_value=CompletedProcess(["powercfg", "/list"], 0, SAMPLE_POWERCFG_LIST, ""),
    ):
        ok, err = set_power_plan("Missing Plan")
        assert ok is False
        assert "not found" in (err or "")


def test_detect_active_game_falls_back_to_fullscreen():
    from deskpilot.boot_tasks.memory_manager import detect_active_game

    with (
        patch("deskpilot.boot_tasks.memory_manager.get_running_process_names", return_value={"notepad.exe"}),
        patch("deskpilot.boot_tasks.memory_manager.detect_fullscreen_foreground_game", return_value="eldenring.exe"),
    ):
        assert detect_active_game(["tslgame.exe"]) == "eldenring.exe"
