"""Tests for the full-screen Textual Bento dashboard (deskpilot.ui.tui_app)."""

from __future__ import annotations

import pytest
from deskpilot.boot_tasks import (
    CalendarAgendaResult,
    CalendarEventItem,
    TempCleanResult,
    WingetUpdateItem,
    WingetUpdateResult,
)
from deskpilot.boot_tasks.news_digest import NewsDigestResult, NewsHeadline
from deskpilot.state import BootState
from deskpilot.ui.tui_app import DeskPilotTUI, render_arc_gauge


def test_render_arc_gauge_alignment():
    """Verify render_arc_gauge has equal leading indentation on all 3 lines."""
    t = render_arc_gauge("15.0 MB")
    lines = t.plain.splitlines()
    assert len(lines) == 3
    # All 3 lines must have 3 leading spaces before border characters
    assert lines[0].startswith("   ╭")
    assert lines[1].startswith("   │")
    assert lines[2].startswith("   ╰")
    assert "15.0 MB" in lines[1]


@pytest.mark.asyncio
async def test_tui_app_compose_empty_state():
    """Verify DeskPilotTUI handles unpopulated/empty boot state safely."""
    app = DeskPilotTUI(state=BootState())
    async with app.run_test() as pilot:
        assert len(app.screen_stack) >= 1


@pytest.mark.asyncio
async def test_tui_app_compose_populated_state():
    """Verify DeskPilotTUI renders populated boot state cleanly."""
    state = BootState()
    state.system_hygiene = TempCleanResult(bytes_freed=1048576, files_removed=10, dirs_removed=2)
    state.winget_updates = WingetUpdateResult(
        total_count=1,
        updates=[WingetUpdateItem(name="Git", id="Git.Git", version="2.0", available_version="2.1")],
    )
    state.calendar_agenda = CalendarAgendaResult(
        events=[CalendarEventItem(id="1", summary="Sprint Sync", start_time="10:00", end_time="10:30")],
        total_count=1,
        is_configured=True,
    )
    state.morning_news = NewsDigestResult(
        headlines=[NewsHeadline(title="New Release", topic="Tech", source="HN", link="https://example.com")],
    )

    app = DeskPilotTUI(state=state)
    async with app.run_test() as pilot:
        assert len(app.screen_stack) >= 1


@pytest.mark.asyncio
async def test_tui_app_keybindings_numeric():
    """Verify pressing numeric action keys exits with the matching action code."""
    for choice in ("1", "2", "3", "4", "5", "6"):
        app = DeskPilotTUI(state=BootState())
        async with app.run_test() as pilot:
            await pilot.press(choice)
            assert app.return_value == int(choice)


@pytest.mark.asyncio
async def test_tui_app_keybindings_exit():
    """Verify pressing exit keys (0, q, escape) exits with 0."""
    for key in ("0", "q", "escape"):
        app = DeskPilotTUI(state=BootState())
        async with app.run_test() as pilot:
            await pilot.press(key)
            assert app.return_value == 0
