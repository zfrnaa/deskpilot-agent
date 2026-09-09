from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from deskpilot.boot_tasks.package_checker import (
    WingetUpdateItem,
    WingetUpdateResult,
    check_winget_updates,
    parse_winget_output,
)
from deskpilot.config import WingetConfig

SAMPLE_WINGET_OUTPUT = """
Name                                        Id                                 Version          Available        Source
-----------------------------------------------------------------------------------------------------------------------
Advanced SystemCare                         IObit.AdvancedSystemCare           < 19.5.0.231     19.5.0.231       winget
CCleaner 7                                  Piriform.CCleaner                  7.9.1432.1847    7.11.1509.1940   winget
Java(TM) SE Development Kit 26.0.1 (64-bit) Oracle.JDK.26                      26.0.1.0         26.0.2.0         winget
Microsoft OLE DB Driver for SQL Server      Microsoft.SQLServer.OLEDBDriver    18.7.5.0         19.4.1.0         winget
Power Automate for desktop                  Microsoft.PowerAutomateDesktop     2.69.00217.26166 2.71.00115.26224 winget
Revo Uninstaller Pro 5.5.0                  RevoUninstaller.RevoUninstallerPro 5.5.0            5.5.2.0          winget
6 upgrades available.
"""

SAMPLE_WINGET_NO_SOURCE = """
Name               Id             Version Available
---------------------------------------------------
Git                Git.Git        2.40.0  2.41.0
Node.js            OpenJS.NodeJS  18.15.0 20.0.0
2 upgrades available.
"""

SAMPLE_WINGET_SPACED_SEPARATOR = """
Name                  Id                  Version       Available     Source
--------------------- ------------------- ------------- ------------- ------
VLC media player      VideoLAN.VLC        3.0.18        3.0.20        winget
1 upgrades available.
"""


def test_models_instantiation():
    """Verify WingetUpdateItem and WingetUpdateResult instantiation and defaults."""
    item = WingetUpdateItem(
        name="Git",
        id="Git.Git",
        version="2.40.0",
        available_version="2.41.0",
        source="winget",
    )
    assert item.name == "Git"
    assert item.id == "Git.Git"
    assert item.version == "2.40.0"
    assert item.available_version == "2.41.0"
    assert item.source == "winget"

    item_default_source = WingetUpdateItem(
        name="Node",
        id="OpenJS.NodeJS",
        version="18.0.0",
        available_version="20.0.0",
    )
    assert item_default_source.source == ""

    res_default = WingetUpdateResult()
    assert res_default.total_count == 0
    assert res_default.updates == []
    assert res_default.timed_out is False
    assert res_default.error is None

    res_custom = WingetUpdateResult(
        total_count=1,
        updates=[item],
        timed_out=False,
        error=None,
    )
    assert res_custom.total_count == 1
    assert len(res_custom.updates) == 1
    assert res_custom.updates[0].id == "Git.Git"


def test_parse_winget_output_standard_table():
    """Verify parsing standard winget table output with multiple columns and source."""
    updates = parse_winget_output(SAMPLE_WINGET_OUTPUT)
    assert len(updates) == 6

    first = updates[0]
    assert first.name == "Advanced SystemCare"
    assert first.id == "IObit.AdvancedSystemCare"
    assert first.version == "< 19.5.0.231"
    assert first.available_version == "19.5.0.231"
    assert first.source == "winget"

    third = updates[2]
    assert third.name == "Java(TM) SE Development Kit 26.0.1 (64-bit)"
    assert third.id == "Oracle.JDK.26"
    assert third.version == "26.0.1.0"
    assert third.available_version == "26.0.2.0"
    assert third.source == "winget"

    last = updates[5]
    assert last.name == "Revo Uninstaller Pro 5.5.0"
    assert last.id == "RevoUninstaller.RevoUninstallerPro"
    assert last.version == "5.5.0"
    assert last.available_version == "5.5.2.0"
    assert last.source == "winget"


def test_parse_winget_output_no_source():
    """Verify parsing table without Source column."""
    updates = parse_winget_output(SAMPLE_WINGET_NO_SOURCE)
    assert len(updates) == 2

    assert updates[0].name == "Git"
    assert updates[0].id == "Git.Git"
    assert updates[0].version == "2.40.0"
    assert updates[0].available_version == "2.41.0"
    assert updates[0].source == ""

    assert updates[1].name == "Node.js"
    assert updates[1].id == "OpenJS.NodeJS"
    assert updates[1].version == "18.15.0"
    assert updates[1].available_version == "20.0.0"
    assert updates[1].source == ""


def test_parse_winget_output_spaced_separator():
    """Verify parsing table where separator line contains spaced dash groups."""
    updates = parse_winget_output(SAMPLE_WINGET_SPACED_SEPARATOR)
    assert len(updates) == 1
    assert updates[0].name == "VLC media player"
    assert updates[0].id == "VideoLAN.VLC"
    assert updates[0].version == "3.0.18"
    assert updates[0].available_version == "3.0.20"
    assert updates[0].source == "winget"


def test_parse_winget_output_with_ansi_escapes():
    """Verify ANSI escape codes are stripped cleanly before parsing."""
    ansi_output = (
        "\x1b[2J\x1b[mName                                        Id                                 Version          Available        Source\x1b[0m\n"
        "-----------------------------------------------------------------------------------------------------------------------\n"
        "\x1b[32mCCleaner 7\x1b[0m                                  Piriform.CCleaner                  7.9.1432.1847    7.11.1509.1940   winget\n"
        "1 upgrades available.\n"
    )
    updates = parse_winget_output(ansi_output)
    assert len(updates) == 1
    assert updates[0].name == "CCleaner 7"
    assert updates[0].id == "Piriform.CCleaner"
    assert updates[0].version == "7.9.1432.1847"
    assert updates[0].available_version == "7.11.1509.1940"


def test_parse_winget_output_empty_and_benign_messages():
    """Verify empty outputs and winget no-match notifications return empty list."""
    assert parse_winget_output("") == []
    assert parse_winget_output("   \n  \t ") == []
    assert parse_winget_output("No installed package found matching input criteria.") == []
    assert parse_winget_output("No applicable update found.") == []
    assert parse_winget_output("No newer package versions found from available sources.") == []


def test_parse_winget_output_headers_only():
    """Verify table with headers and separator but no packages returns empty list."""
    headers_only = """
Name    Id    Version    Available    Source
--------------------------------------------
0 upgrades available.
"""
    assert parse_winget_output(headers_only) == []


def test_parse_winget_output_filters_informational_footers():
    """Verify footer notes with package(s) and pins are discarded without preceding upgrades line."""
    output_with_footers = """
Name                  Id                  Version       Available     Source
--------------------- ------------------- ------------- ------------- ------
VLC media player      VideoLAN.VLC        3.0.18        3.0.20        winget
2 package(s) have version numbers that cannot be determined. Use --include-unknown to see all results.
1 package(s) have pins that prevent upgrade.
"""
    updates = parse_winget_output(output_with_footers)
    assert len(updates) == 1
    assert updates[0].name == "VLC media player"
    assert updates[0].id == "VideoLAN.VLC"
    assert not any("package(s)" in item.name for item in updates)

    # Verify when output contains only footers without package rows or upgrades available line
    footers_only = """
Name                  Id                  Version       Available     Source
--------------------- ------------------- ------------- ------------- ------
2 package(s) have version numbers that cannot be determined. Use --include-unknown to see all results.
1 package(s) have pins that prevent upgrade.
"""
    assert parse_winget_output(footers_only) == []

    # Verify reversed footer order where pins line appears first
    footers_pins_first = """
Name                  Id                  Version       Available     Source
--------------------- ------------------- ------------- ------------- ------
1 package(s) have pins that prevent upgrade.
2 package(s) have version numbers that cannot be determined. Use --include-unknown to see all results.
"""
    assert parse_winget_output(footers_pins_first) == []


@pytest.mark.asyncio
async def test_check_winget_updates_success():
    """Verify check_winget_updates executes winget command and parses packages."""
    with patch("deskpilot.boot_tasks.package_checker.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout=SAMPLE_WINGET_OUTPUT,
            stderr="",
            returncode=0,
        )

        result = await check_winget_updates(timeout_secs=10)

        mock_run.assert_called_once_with(
            ["winget", "upgrade", "--include-unknown"],
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        )
        assert result.total_count == 6
        assert len(result.updates) == 6
        assert result.timed_out is False
        assert result.error is None
        assert result.updates[0].id == "IObit.AdvancedSystemCare"


@pytest.mark.asyncio
async def test_check_winget_updates_empty_results():
    """Verify benign empty results like 'No installed package found' return count 0 and no error."""
    with patch("deskpilot.boot_tasks.package_checker.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout="No installed package found matching input criteria.\n",
            stderr="",
            returncode=0,
        )

        result = await check_winget_updates()
        assert result.total_count == 0
        assert result.updates == []
        assert result.timed_out is False
        assert result.error is None


@pytest.mark.asyncio
async def test_check_winget_updates_timeout():
    """Verify subprocess timeout is caught and reported gracefully."""
    with patch("deskpilot.boot_tasks.package_checker.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["winget"], timeout=15)

        result = await check_winget_updates(timeout_secs=15)
        assert result.timed_out is True
        assert result.total_count == 0
        assert result.updates == []
        assert result.error is not None
        assert "timed out" in result.error


@pytest.mark.asyncio
async def test_check_winget_updates_missing_executable():
    """Verify missing winget binary raises FileNotFoundError and is handled gracefully."""
    with patch("deskpilot.boot_tasks.package_checker.subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError("winget executable not found")

        result = await check_winget_updates()
        assert result.timed_out is False
        assert result.total_count == 0
        assert result.updates == []
        assert result.error == "winget executable not found"


@pytest.mark.asyncio
async def test_check_winget_updates_os_error():
    """Verify general OSError is caught and reported in error field."""
    with patch("deskpilot.boot_tasks.package_checker.subprocess.run") as mock_run:
        mock_run.side_effect = OSError("Access denied: win32 error")

        result = await check_winget_updates()
        assert result.timed_out is False
        assert result.total_count == 0
        assert result.updates == []
        assert "Access denied" in (result.error or "")


@pytest.mark.asyncio
async def test_check_winget_updates_with_config_disabled():
    """Verify when config.enabled is False, subprocess is skipped."""
    with patch("deskpilot.boot_tasks.package_checker.subprocess.run") as mock_run:
        config = WingetConfig(enabled=False)
        result = await check_winget_updates(config=config)

        mock_run.assert_not_called()
        assert result.total_count == 0
        assert result.updates == []
        assert result.timed_out is False
        assert result.error is None


@pytest.mark.asyncio
async def test_check_winget_updates_with_config_custom_timeout():
    """Verify timeout_secs from WingetConfig is used when default timeout_secs is passed."""
    with patch("deskpilot.boot_tasks.package_checker.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout=SAMPLE_WINGET_NO_SOURCE,
            stderr="",
            returncode=0,
        )
        config = WingetConfig(enabled=True, timeout_secs=25)
        result = await check_winget_updates(config=config)

        mock_run.assert_called_once_with(
            ["winget", "upgrade", "--include-unknown"],
            capture_output=True,
            text=True,
            timeout=25,
            encoding="utf-8",
            errors="replace",
        )
        assert result.total_count == 2


@pytest.mark.asyncio
async def test_check_winget_updates_non_zero_exit_with_error():
    """Verify non-zero returncode with unexpected stderr records error."""
    with patch("deskpilot.boot_tasks.package_checker.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout="",
            stderr="Failed to update winget catalog: network unreachable",
            returncode=1,
        )

        result = await check_winget_updates()
        assert result.total_count == 0
        assert result.updates == []
        assert result.timed_out is False
        assert result.error is not None
        assert "network unreachable" in result.error


def test_parse_winget_output_filters_ignore_packages_by_name_and_id():
    """Verify parse_winget_output drops packages matching ignore_packages."""
    updates = parse_winget_output(
        SAMPLE_WINGET_OUTPUT,
        ignore_packages=["AdvancedSystemCare", "RevoUninstallerPro"],
    )
    assert len(updates) == 4
    names = [u.name for u in updates]
    ids = [u.id for u in updates]
    assert "Advanced SystemCare" not in names
    assert "IObit.AdvancedSystemCare" not in ids
    assert "Revo Uninstaller Pro 5.5.0" not in names
    assert "RevoUninstaller.RevoUninstallerPro" not in ids
    assert "CCleaner 7" in names
    assert "Power Automate for desktop" in names


def test_parse_winget_output_ignore_packages_case_insensitive_substring():
    """Verify ignore_packages performs case-insensitive substring matches on name and id."""
    # Substring in id (lowercase)
    updates_id = parse_winget_output(
        SAMPLE_WINGET_OUTPUT,
        ignore_packages=["advancedsystemcare"],
    )
    assert len(updates_id) == 5
    assert not any("AdvancedSystemCare" in u.id for u in updates_id)

    # Substring in name
    updates_name = parse_winget_output(
        SAMPLE_WINGET_OUTPUT,
        ignore_packages=["power automate"],
    )
    assert len(updates_name) == 5
    assert not any("Power Automate" in u.name for u in updates_name)

    # Substring in id (Revo)
    updates_revo = parse_winget_output(
        SAMPLE_WINGET_OUTPUT,
        ignore_packages=["revouninstallerpro"],
    )
    assert len(updates_revo) == 5
    assert not any("RevoUninstallerPro" in u.id for u in updates_revo)


def test_parse_winget_output_ignore_packages_none_or_empty():
    """Verify passing None or empty list to ignore_packages returns all packages."""
    updates_none = parse_winget_output(SAMPLE_WINGET_OUTPUT, ignore_packages=None)
    assert len(updates_none) == 6

    updates_empty = parse_winget_output(SAMPLE_WINGET_OUTPUT, ignore_packages=[])
    assert len(updates_empty) == 6


@pytest.mark.asyncio
async def test_check_winget_updates_applies_config_ignore_packages():
    """Verify check_winget_updates passes config.ignore_packages to drop excluded packages."""
    with patch("deskpilot.boot_tasks.package_checker.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout=SAMPLE_WINGET_OUTPUT,
            stderr="",
            returncode=0,
        )

        config = WingetConfig()  # defaults to ["AdvancedSystemCare", "RevoUninstallerPro"]
        result = await check_winget_updates(config=config)

        assert result.total_count == 4
        assert len(result.updates) == 4
        ids = [u.id for u in result.updates]
        assert "IObit.AdvancedSystemCare" not in ids
        assert "RevoUninstaller.RevoUninstallerPro" not in ids


def test_no_utf8_bom_in_package_checker_files():
    """Verify no UTF-8 BOM headers exist in checker module or test file."""
    project_root = Path(__file__).parent.parent
    py_files = [
        project_root / "deskpilot" / "boot_tasks" / "package_checker.py",
        project_root / "tests" / "test_package_checker.py",
    ]
    for py_file in py_files:
        if py_file.exists():
            raw_bytes = py_file.read_bytes()
            assert not raw_bytes.startswith(b"\xef\xbb\xbf"), f"BOM found in {py_file}"
