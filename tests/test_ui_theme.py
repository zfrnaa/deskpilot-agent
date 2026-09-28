"""Tests for the DeskPilot visual design system (deskpilot.ui).

The existing suite in test_cli.py must keep passing untouched, so these tests
lock in the *new* behaviour only: theme tokens, glyph/encoding degradation, and
the boot animation's ambient sink contract.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from rich.console import Console

from deskpilot.boot_tasks import (
    BookmarkAuditResult,
    CalendarAgendaResult,
    CalendarEventItem,
    TempCleanResult,
    WingetUpdateItem,
    WingetUpdateResult,
)
from deskpilot.cli import main_async, render_dashboard, run_phase1_boot_sequence
from deskpilot.config import Settings
from deskpilot.state import BootState
from deskpilot.ui import art, progress, theme
from deskpilot.ui.progress import (
    BOOT_TASK_LABELS,
    DONE,
    FAILED,
    PENDING,
    animation_enabled,
    boot_animation,
    current_sink,
)

ROOT = Path(__file__).parent.parent
UI_FILES = [
    ROOT / "deskpilot" / "ui" / "__init__.py",
    ROOT / "deskpilot" / "ui" / "theme.py",
    ROOT / "deskpilot" / "ui" / "art.py",
    ROOT / "deskpilot" / "ui" / "progress.py",
]


def _populated_state() -> BootState:
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
        date_str="Saturday, September 13",
    )
    state.floorp_bookmarks = BookmarkAuditResult(
        total_bookmarks=120, noisy_count=7, duplicate_groups_count=3
    )
    return state


def _string_console(width: int = 100) -> Console:
    return Console(file=io.StringIO(), width=width, theme=theme.DESKPILOT_THEME)


# --- Theme tokens ----------------------------------------------------------


def test_theme_exposes_all_tokens():
    """Verify the Catppuccin palette and chrome constants are all present."""
    for token in ("BG", "MANTLE", "CRUST", "SURFACE", "OVERLAY", "TEXT", "SUBTEXT"):
        assert getattr(theme, token).startswith("#")
    for token in ("BLUE", "LAVENDER", "SAPPHIRE", "TEAL", "GREEN", "YELLOW", "RED", "MAUVE"):
        assert len(getattr(theme, token)) == 7
    assert theme.PANEL_BOX is not None
    assert theme.PANEL_STYLE == f"on {theme.BG}"
    assert theme.PRIMARY in (theme.SAPPHIRE, theme.LAVENDER)
    assert theme.GLYPH_BLOCK == "\u2588"


def test_theme_retints_builtin_markup_names():
    """Verify rich resolves both bare and bold colour tags to the new palette."""
    console = Console(theme=theme.DESKPILOT_THEME)
    for name, expected in (
        ("cyan", theme.TEAL),
        ("magenta", theme.MAUVE),
        ("blue", theme.BLUE),
        ("green", theme.GREEN),
        ("yellow", theme.YELLOW),
        ("red", theme.RED),
    ):
        assert console.get_style(name).color.name == expected
        assert console.get_style(f"bold {name}").color.name == expected


# --- Glyph / encoding degradation -----------------------------------------


def test_supports_glyphs_detects_cp1252_stream():
    """Verify a cp1252 stream is detected as glyph-incapable and utf-8 is not."""
    cp1252 = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="replace")
    utf8 = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    assert theme.supports_glyphs(cp1252) is False
    assert theme.supports_glyphs(utf8) is True


def test_wordmark_degrades_to_ascii_art():
    """Verify the wordmark falls back to ASCII blocks when unicode is unavailable."""
    unicode_body = art.render_wordmark(unicode_ok=True)
    ascii_body = art.render_wordmark(unicode_ok=False)
    assert theme.GLYPH_BLOCK in unicode_body.plain
    assert theme.GLYPH_BLOCK not in ascii_body.plain
    assert "#" in ascii_body.plain
    assert unicode_body.plain.count("\n") == ascii_body.plain.count("\n")


def test_wordmark_degrades_below_minimum_width():
    """Verify narrow terminals get a single banner line that still says DeskPilot."""
    console = _string_console(width=60)
    console.print(art.make_header("Saturday, September 13, 2026 - 09:14 AM", 60))
    out = console.file.getvalue()
    assert "DeskPilot" in out
    assert theme.GLYPH_BLOCK not in out


def test_header_fits_and_keeps_block_art_at_test_widths():
    """Verify the block-art header never wraps at the widths the suite uses."""
    for width in (100, 120):
        console = _string_console(width=width)
        console.print(art.make_header("Saturday, September 13, 2026 - 09:14 AM", width))
        out = console.file.getvalue()
        assert max(len(line) for line in out.splitlines()) <= width
        assert sum(1 for line in out.splitlines() if theme.GLYPH_BLOCK in line) == 5
        assert "DeskPilot Morning Command Center" in out


def test_panel_drops_icon_when_stream_cannot_encode_it():
    """Verify the emoji icon is omitted rather than crashing a cp1252 stream."""
    cp1252 = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="replace")
    console = Console(file=cp1252, width=80, theme=theme.DESKPILOT_THEME)
    original_stdout = sys.stdout
    sys.stdout = cp1252
    try:
        console.print(theme.panel("body", "System Hygiene (%TEMP%)", "cyan", theme.ICON_HYGIENE))
        console.file.flush()
    finally:
        sys.stdout = original_stdout
    out = cp1252.buffer.getvalue().decode("cp1252")
    assert "System Hygiene (%TEMP%)" in out
    assert theme.ICON_HYGIENE not in out


# --- Boot animation gating -------------------------------------------------


def test_animation_disabled_when_console_is_not_a_terminal():
    """Verify recorded/piped consoles run the boot phase with no animation."""
    console = Console(record=True, width=100)
    assert animation_enabled(console) is False
    with boot_animation(console, enabled=animation_enabled(console), labels=BOOT_TASK_LABELS):
        assert current_sink() is None
    assert "Booting DeskPilot" not in console.export_text()


def test_animation_disabled_in_startup_mode():
    """Verify the Windows logon path never animates."""
    console = Console(file=io.StringIO(), force_terminal=True, width=100)
    assert console.is_terminal is True
    assert animation_enabled(console, startup=True) is False


def test_animation_disabled_by_env_var(monkeypatch):
    """Verify DESKPILOT_NO_ANIMATION=1 is an explicit kill switch."""
    console = Console(file=io.StringIO(), force_terminal=True, width=100)
    assert animation_enabled(console) is True
    monkeypatch.setenv("DESKPILOT_NO_ANIMATION", "1")
    assert animation_enabled(console) is False


# --- Ambient sink contract -------------------------------------------------


@pytest.mark.asyncio
async def test_tracked_tasks_are_passthrough_without_a_sink():
    """Verify run_phase1_boot_sequence still returns (settings)-only results."""
    settings = Settings()
    with (
        patch("deskpilot.cli.clean_temp_directory", new_callable=AsyncMock) as mock_temp,
        patch("deskpilot.cli.check_winget_updates", new_callable=AsyncMock) as mock_winget,
        patch("deskpilot.cli.fetch_today_agenda", new_callable=AsyncMock) as mock_cal,
    ):
        mock_temp.return_value = TempCleanResult(bytes_freed=1024, files_removed=1, dirs_removed=0)
        mock_winget.return_value = WingetUpdateResult(total_count=0, updates=[])
        mock_cal.side_effect = RuntimeError("calendar offline")
        assert current_sink() is None
        state = await run_phase1_boot_sequence(settings)

    assert state.system_hygiene.files_removed == 1
    assert state.winget_updates.total_count == 0
    assert state.has_errors()
    assert any("Calendar briefing failed" in err for err in state.errors)


@pytest.mark.asyncio
async def test_sink_records_begin_finish_and_failure():
    """Verify the boot sink tracks per-task status and is cleaned up afterwards."""
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=True, width=100)
    with boot_animation(console, enabled=True, labels=BOOT_TASK_LABELS) as sink:
        assert sink is not None
        assert current_sink() is sink
        sink.begin(BOOT_TASK_LABELS[0])
        assert sink.tasks[0].status == progress.RUNNING
        sink.finish(BOOT_TASK_LABELS[0])
        sink.begin(BOOT_TASK_LABELS[1])
        sink.fail(BOOT_TASK_LABELS[1], "boom")
        assert [task.status for task in sink.tasks] == [DONE, FAILED, PENDING]
        assert sink.tasks[1].error == "boom"

    assert current_sink() is None
    assert "Booting DeskPilot" in buffer.getvalue()


@pytest.mark.asyncio
async def test_main_async_animates_boot_on_a_real_terminal():
    """Verify main_async wires the animation in without changing boot call args."""
    settings = Settings()
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=True, width=100, theme=theme.DESKPILOT_THEME)

    def raise_eof(_: str) -> str:
        raise EOFError()

    with (
        patch("deskpilot.cli.load_settings", return_value=settings),
        patch("deskpilot.cli.run_phase1_boot_sequence", new_callable=AsyncMock) as mock_boot,
    ):
        mock_boot.return_value = BootState()
        exit_code = await main_async(console=console, settings=settings, prompt_func=raise_eof)

    assert exit_code == 0
    mock_boot.assert_awaited_once_with(settings)
    assert "Booting DeskPilot" in buffer.getvalue()


@pytest.mark.asyncio
async def test_main_async_startup_never_animates():
    """Verify --startup renders the dashboard with no animation artefacts."""
    settings = Settings()
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=True, width=100, theme=theme.DESKPILOT_THEME)

    with (
        patch("deskpilot.cli.load_settings", return_value=settings),
        patch("deskpilot.cli.run_phase1_boot_sequence", new_callable=AsyncMock) as mock_boot,
    ):
        mock_boot.return_value = BootState()
        exit_code = await main_async(console=console, settings=settings, startup=True)

    assert exit_code == 0
    out = buffer.getvalue()
    assert "Booting DeskPilot" not in out
    assert "DeskPilot" in out


# --- Regression tripwires --------------------------------------------------


def test_render_dashboard_preserves_contract_strings():
    """Verify restyling kept every string the existing suite asserts on."""
    console = Console(record=True, width=120, theme=theme.DESKPILOT_THEME)
    render_dashboard(_populated_state(), console=console)
    out = console.export_text()

    assert "DeskPilot" in out
    assert "System Hygiene" in out
    assert "15.0 MB" in out
    assert "42" in out
    assert "Package Updates" in out
    assert "Git" in out
    assert "Neovim" in out
    assert "Agenda" in out
    assert "Daily Standup" in out
    assert "Floorp" in out
    assert "120" in out

    boot_console = Console(record=True, width=120, theme=theme.DESKPILOT_THEME)
    render_dashboard(BootState(), console=boot_console)
    boot_out = boot_console.export_text()
    assert "not executed" in boot_out.lower()
    assert "Floorp" not in boot_out


def test_panel_chrome_carries_the_palette_into_rendered_segments():
    """Verify panels carry the painted background and the themed border colour.

    Segments are inspected rather than the final ANSI text because rich\'s
    legacy-Windows writer downgrades truecolor to 16 colours at write time in
    non-console environments (including pytest), while the segment stream keeps
    the palette a real terminal receives.
    """
    console = Console(
        file=io.StringIO(),
        force_terminal=True,
        color_system="truecolor",
        width=100,
        theme=theme.DESKPILOT_THEME,
    )
    panel = theme.panel("body", "System Hygiene (%TEMP%)", "cyan", theme.ICON_HYGIENE)
    segments = list(console.render(panel))
    colors = {seg.style.color.name for seg in segments if seg.style and seg.style.color}
    backgrounds = {seg.style.bgcolor.name for seg in segments if seg.style and seg.style.bgcolor}
    assert theme.BG in backgrounds, "painted panel background missing"
    assert theme.TEAL in colors, "themed hygiene border missing"


def test_header_wordmark_renders_a_multi_stop_gradient():
    """Verify the wordmark sweeps many distinct shades rather than one colour."""
    console = Console(
        file=io.StringIO(),
        force_terminal=True,
        color_system="truecolor",
        width=100,
        theme=theme.DESKPILOT_THEME,
    )
    header = art.make_header("Saturday, September 13, 2026 - 09:14 AM", 100)
    segments = list(console.render(header))
    shades = {seg.style.color.name for seg in segments if seg.style and seg.style.color}
    assert len(shades) > 10, f"expected a gradient, got {sorted(shades)}"


def test_render_dashboard_emits_ansi_on_a_terminal():
    """Verify the dashboard is colourised rather than emitted as plain text."""
    buffer = io.StringIO()
    console = Console(
        file=buffer,
        force_terminal=True,
        color_system="truecolor",
        width=100,
        theme=theme.DESKPILOT_THEME,
    )
    render_dashboard(_populated_state(), console=console)
    out = buffer.getvalue()
    assert "\x1b[" in out
    assert "System Hygiene" in out


def test_detect_color_system_requests_truecolor_for_windows_terminal(monkeypatch):
    """Verify WT_SESSION on a tty yields truecolor, and redirects stay plain."""
    class _FakeStream:
        def __init__(self, tty: bool) -> None:
            self._tty = tty

        def isatty(self) -> bool:
            return self._tty

    monkeypatch.delenv("COLORTERM", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    monkeypatch.setenv("WT_SESSION", "55f14797-de2b-44dd-972a-e4bdaa2b8dd0")
    assert theme.detect_color_system(_FakeStream(True)) == "truecolor"
    assert theme.detect_color_system(_FakeStream(False)) is None
    monkeypatch.delenv("WT_SESSION", raising=False)
    assert theme.detect_color_system(_FakeStream(True)) is None


def test_detect_color_system_defers_when_environment_is_descriptive(monkeypatch):
    """Verify an explicit COLORTERM/TERM is left to rich to interpret.

    Note: extracting through os.environ keeps this test off the ambient shell.
    """
    import os as _os

    env = dict(_os.environ)
    env["COLORTERM"] = "truecolor"
    assert theme.detect_color_system(io.StringIO(), environ=env) is None
    env.pop("COLORTERM")
    env["TERM"] = "xterm-256color"
    assert theme.detect_color_system(io.StringIO(), environ=env) is None
    env.pop("TERM")
    env["WT_SESSION"] = "abc"
    assert theme.detect_color_system(io.StringIO(), environ=env) is None


def test_detect_color_system_and_get_console_env_overrides(monkeypatch):
    """Verify DESKPILOT_COLOR_SYSTEM and DESKPILOT_FORCE_COLOR take effect."""
    monkeypatch.setenv("DESKPILOT_COLOR_SYSTEM", "truecolor")
    assert theme.detect_color_system(io.StringIO()) == "truecolor"

    monkeypatch.setenv("DESKPILOT_FORCE_COLOR", "1")
    c = theme.get_console()
    assert c.color_system == "truecolor"
    assert c.is_terminal is True


def test_ui_package_does_not_import_langgraph():
    """Verify importing the whole UI package stays off the heavy boot path."""
    code = (
        "import sys; import deskpilot.ui.theme, deskpilot.ui.art, deskpilot.ui.progress, deskpilot.cli; "
        "assert 'langgraph' not in sys.modules, 'langgraph eagerly loaded'"
    )
    subprocess.run([sys.executable, "-c", code], check=True, cwd=str(ROOT), capture_output=True)


def test_ui_sources_are_ascii_without_bom():
    """Verify the UI sources stay ASCII-only and BOM-free (Windows safety)."""
    for path in UI_FILES:
        assert path.exists(), f"missing {path}"
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), f"{path.name} has a UTF-8 BOM"
        assert raw.isascii(), f"{path.name} contains non-ASCII bytes"