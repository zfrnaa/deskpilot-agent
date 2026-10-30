"""Textual Full-Screen Modern TUI Dashboard for DeskPilot.

Matches the Bento-box visual concept mockup:
- Top Header: Sleek stylized 'DESKPILOT' wordmark & timestamp
- Left Column:
    1. Agent Workflow Pipeline: Rounded pill cards with step connectors
    2. Curated Morning News Briefing: Real-time intelligence feed
- Right Column:
    1. System Hygiene: Semi-circular metric arc & stats
    2. Daily Digest & Calendar: Timeline markers with priority chips
    3. Package Updates: Version delta pills
- Bottom Command Dock: Interactive hotkey bar [1] Triage [2] Clean [3] Floorp [4] Winget [5] Notion [0] Exit
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from deskpilot.boot_tasks import (
    BookmarkAuditResult,
    CalendarAgendaResult,
    TempCleanResult,
    WingetUpdateResult,
)
from deskpilot.boot_tasks.news_digest import NewsDigestResult
from deskpilot.config import Settings, load_settings
from deskpilot.state import BootState


DASHBOARD_CSS = """
Screen {
    background: #0d1117;
    color: #c9d1d9;
    layout: vertical;
    padding: 1 2;
}

#top-bar {
    height: 3;
    layout: horizontal;
    align: left middle;
    margin-bottom: 1;
}

#logo-title {
    color: #58a6ff;
    text-style: bold;
    width: auto;
    content-align: left middle;
}

#time-badge {
    color: #8b949e;
    width: auto;
    margin-left: 2;
    content-align: left middle;
}

#bento-container {
    height: 1fr;
    layout: horizontal;
}

#left-column {
    width: 54%;
    margin-right: 1;
    layout: vertical;
}

#right-column {
    width: 46%;
    layout: vertical;
}

.bento-card {
    border: round #30363d;
    background: #161b22;
    padding: 1 2;
    margin-bottom: 1;
}

.bento-card:focus {
    border: round #58a6ff;
}

.card-title {
    color: #58a6ff;
    text-style: bold;
    margin-bottom: 1;
}

#pipeline-card {
    height: 7;
}

#news-card {
    height: 1fr;
}

#hygiene-card {
    height: 9;
}

#calendar-card {
    height: 9;
}

#winget-card {
    height: 1fr;
}

#dock-bar {
    height: 3;
    border-top: solid #30363d;
    layout: horizontal;
    align: left middle;
    color: #8b949e;
    padding-top: 1;
}

.dock-chip {
    color: #79c0ff;
    text-style: bold;
    margin-right: 2;
}
"""


def format_bytes_str(size: int) -> str:
    """Format bytes into readable string."""
    for unit in ["B", "KB", "MB", "GB"]:
        if abs(size) < 1024.0:
            return f"{size:3.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def render_arc_gauge(freed_str: str) -> Text:
    """Render the circular/semi-circular arc gauge matching the concept mockup."""
    #   ╭─────╮
    #  │ 1.2 GB│
    #  ╰─Freed─╯
    t = Text()
    t.append("   ╭──────────╮\n", style="bold #79c0ff")
    t.append("  │ ", style="bold #79c0ff")
    t.append(f"{freed_str:^8}", style="bold #ffffff")
    t.append(" │\n", style="bold #79c0ff")
    t.append("   ╰──Freed───╯", style="bold #58a6ff")
    return t


class DeskPilotTUI(App):
    """Modern Bento Terminal Dashboard for DeskPilot."""

    CSS = DASHBOARD_CSS
    TITLE = "DESKPILOT"

    def __init__(
        self,
        state: BootState | None = None,
        settings: Settings | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.state = state or BootState()
        self.settings = settings or load_settings()

    def compose(self) -> ComposeResult:
        """Compose the full Bento dashboard."""
        time_str = self.state.timestamp.strftime("%A, %B %d, %Y - %I:%M %p")

        # 1. Top HUD Header
        with Horizontal(id="top-bar"):
            yield Static("⚡ DESKPILOT", id="logo-title")
            yield Static(f"│  Morning Command Center  │  {time_str}", id="time-badge")

        # 2. Main Two-Column Bento Grid
        with Horizontal(id="bento-container"):
            # Left Column (Workflow Pipeline + News Feed)
            with Vertical(id="left-column"):
                with Container(classes="bento-card", id="pipeline-card"):
                    yield Static("Agent Workflow Pipeline", classes="card-title")
                    pipe_text = Text()
                    pipe_text.append("[ ● Ingest Watcher ]", style="bold #56d364")
                    pipe_text.append(" ───→ ", style="bold #58a6ff")
                    pipe_text.append("[ ◎ Vision Triage ]", style="bold #79c0ff")
                    pipe_text.append(" ───→ ", style="dim")
                    pipe_text.append("[ ○ Notion Sync ]", style="dim")
                    pipe_text.append("\n\n▸ Orchestrator active. Awaiting manual trigger or scheduled queue.", style="dim")
                    yield Static(pipe_text)

                with Container(classes="bento-card", id="news-card"):
                    yield Static("Morning Intelligence Briefing (Top Headlines)", classes="card-title")
                    mn = getattr(self.state, "morning_news", None)
                    news_text = Text()
                    if mn and isinstance(mn, NewsDigestResult) and mn.headlines:
                        for item in mn.headlines[:5]:
                            news_text.append(" ◆ ", style="bold #e3b341")
                            news_text.append(f"[{item.topic}] ", style="bold #d2a8ff")
                            news_text.append(f"{item.title} ", style="bold #ffffff")
                            if item.source:
                                news_text.append(f"via {item.source} ", style="dim")
                            news_text.append("\n")
                    else:
                        news_text.append("No active news stories fetched or network offline.\n", style="dim")
                    yield Static(news_text)

            # Right Column (System Hygiene + Calendar + Winget)
            with Vertical(id="right-column"):
                with Container(classes="bento-card", id="hygiene-card"):
                    yield Static("System Hygiene (%TEMP%)", classes="card-title")
                    sh: TempCleanResult | None = self.state.system_hygiene
                    freed = format_bytes_str(sh.bytes_freed) if sh and isinstance(sh, TempCleanResult) else "0 B"
                    files_cnt = sh.files_removed if sh and isinstance(sh, TempCleanResult) else 0
                    dirs_cnt = sh.dirs_removed if sh and isinstance(sh, TempCleanResult) else 0

                    hyg_table = Text()
                    hyg_table.append("Status: ", style="dim")
                    hyg_table.append("● Cleared\n", style="bold #56d364")
                    hyg_table.append(f"Items:  {files_cnt} files • {dirs_cnt} dirs\n", style="dim")
                    hyg_table.append(f"Storage Freed: ", style="dim")
                    hyg_table.append(f"{freed}\n", style="bold #79c0ff")

                    with Horizontal():
                        yield Static(hyg_table, classes="hygiene-text")
                        yield Static(render_arc_gauge(freed), classes="hygiene-arc")

                with Container(classes="bento-card", id="calendar-card"):
                    yield Static("Daily Digest & Calendar", classes="card-title")
                    ca: CalendarAgendaResult | None = self.state.calendar_agenda
                    cal_text = Text()
                    if ca and isinstance(ca, CalendarAgendaResult) and ca.events:
                        cal_text.append(f"Timeline Schedule ({ca.date_str or 'Today'}):\n", style="dim")
                        for ev in ca.events[:3]:
                            time_chip = f"[{ev.start_time} - {ev.end_time}]" if not ev.is_all_day else "[All Day]"
                            cal_text.append(f" • {time_chip} ", style="bold #79c0ff")
                            cal_text.append(f"{ev.summary}\n", style="#ffffff")
                    else:
                        cal_text.append("● Clear Schedule\n", style="bold #56d364")
                        cal_text.append("No meetings or events scheduled for today.\n", style="dim")
                    yield Static(cal_text)

                with Container(classes="bento-card", id="winget-card"):
                    yield Static("Package Updates (winget)", classes="card-title")
                    wu: WingetUpdateResult | None = self.state.winget_updates
                    wu_text = Text()
                    if wu and isinstance(wu, WingetUpdateResult) and wu.total_count > 0:
                        wu_text.append(f"● {wu.total_count} Updates Pending\n", style="bold #e3b341")
                        for item in wu.updates[:4]:
                            wu_text.append(f" ▸ {item.name} ", style="bold #ffffff")
                            wu_text.append(f"({item.version} → ", style="dim")
                            wu_text.append(f"{item.available_version})\n", style="bold #56d364")
                        if len(wu.updates) > 4:
                            wu_text.append(f"   ... and {len(wu.updates) - 4} more updates\n", style="dim")
                    else:
                        wu_text.append("● Up to date\n", style="bold #56d364")
                        wu_text.append("All packages and system binaries are up to date.\n", style="dim")
                    yield Static(wu_text)

        # 3. Bottom Command & Shortcut Dock
        with Horizontal(id="dock-bar"):
            yield Static(Text("Hotkeys:  ", style="dim"))
            yield Static("[1] Screenshots", classes="dock-chip")
            yield Static("[2] Downloads", classes="dock-chip")
            yield Static("[3] Floorp", classes="dock-chip")
            yield Static("[4] Winget", classes="dock-chip")
            yield Static("[5] Read Later", classes="dock-chip")
            yield Static("[0/Q] Dismiss & Exit", classes="dock-chip")

    def on_key(self, event) -> None:
        """Handle keyboard navigation and option selection."""
        key = event.key.lower()
        if key in ("0", "q", "escape"):
            self.exit(result=0)
        elif key in ("1", "2", "3", "4", "5", "6"):
            self.exit(result=int(key))


async def run_tui_dashboard(
    state: BootState | None = None,
    settings: Settings | None = None,
) -> int:
    """Run the modern Textual full-screen dashboard inside an existing asyncio event loop."""
    app = DeskPilotTUI(state=state, settings=settings)
    result = await app.run_async()
    return result if isinstance(result, int) else 0
