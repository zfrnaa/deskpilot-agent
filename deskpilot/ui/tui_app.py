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

from typing import Any

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Input, Static

from deskpilot.boot_tasks import (
    CalendarAgendaResult,
    TempCleanResult,
    WingetUpdateResult,
)
from deskpilot.boot_tasks.news_digest import NewsDigestResult
from deskpilot.config import Settings, load_settings
from deskpilot.state import BootState, format_bytes


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
    overflow-y: auto;
}

#right-column {
    width: 46%;
    layout: vertical;
    overflow-y: auto;
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

#dock-container {
    height: 5;
    border-top: solid #30363d;
    padding-top: 1;
}

#chips-row {
    height: 1;
    layout: horizontal;
}

#chips-row Static {
    width: auto;
    margin-right: 2;
}

.dock-chip {
    color: #79c0ff;
    text-style: bold;
}

.dock-exit {
    color: #e3b341;
    text-style: bold;
}

#input-row {
    height: 3;
    layout: horizontal;
    align: left middle;
    margin-top: 1;
}

#prompt-symbol {
    width: auto;
    color: #56d364;
    text-style: bold;
    margin-right: 1;
}

#prompt-input {
    width: 1fr;
    height: 1;
    border: none;
    background: #161b22;
    color: #ffffff;
}

#prompt-input:focus {
    border: none;
}
"""


def render_arc_gauge(freed_str: str) -> Text:
    """Render the circular/semi-circular arc gauge matching the concept mockup."""
    #   ╭──────────╮
    #   │  1.2 GB  │
    #   ╰──Freed───╯
    t = Text()
    t.append("   ╭──────────╮\n", style="bold #79c0ff")
    t.append("   │ ", style="bold #79c0ff")
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
                    hyg_table = Text()
                    if sh is not None and isinstance(sh, TempCleanResult):
                        freed = format_bytes(sh.bytes_freed)
                        hyg_table.append("Status: ", style="dim")
                        hyg_table.append("● Cleared\n", style="bold #56d364")
                        hyg_table.append(f"Items:  {sh.files_removed} files • {sh.dirs_removed} dirs\n", style="dim")
                        hyg_table.append("Storage Freed: ", style="dim")
                        hyg_table.append(f"{freed}\n", style="bold #79c0ff")
                        gauge_freed = freed
                    else:
                        hyg_table.append("Status: ", style="dim")
                        hyg_table.append("○ Not executed / Disabled\n", style="dim")
                        hyg_table.append("Items:  0 files • 0 dirs\n", style="dim")
                        gauge_freed = "0 B"

                    with Horizontal():
                        yield Static(hyg_table)
                        yield Static(render_arc_gauge(gauge_freed))

                with Container(classes="bento-card", id="calendar-card"):
                    yield Static("Daily Digest & Calendar", classes="card-title")
                    ca: CalendarAgendaResult | None = self.state.calendar_agenda
                    cal_text = Text()
                    if ca is None or not isinstance(ca, CalendarAgendaResult):
                        cal_text.append("○ Agenda not fetched / Disabled\n", style="dim")
                    elif ca.error:
                        cal_text.append("● Calendar error: ", style="bold #f85149")
                        cal_text.append(f"{ca.error}\n", style="dim")
                    elif not ca.is_configured:
                        cal_text.append("● Token unconfigured / Auth required\n", style="bold #e3b341")
                        cal_text.append("Re-authorize in browser via settings.\n", style="dim")
                    elif ca.events:
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
                    if wu is None or not isinstance(wu, WingetUpdateResult):
                        wu_text.append("○ Check not executed / Disabled\n", style="dim")
                    elif wu.timed_out:
                        wu_text.append("● Query timed out\n", style="bold #e3b341")
                        wu_text.append("Run winget upgrade manually.\n", style="dim")
                    elif wu.error:
                        wu_text.append("● Check failed: ", style="bold #f85149")
                        wu_text.append(f"{wu.error}\n", style="dim")
                    elif wu.total_count > 0:
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
        with Vertical(id="dock-container"):
            with Horizontal(id="chips-row"):
                yield Static(Text("Hotkeys:  ", style="dim"))
                yield Static("[1] Screenshots", classes="dock-chip")
                yield Static("[2] Downloads", classes="dock-chip")
                yield Static("[3] Floorp", classes="dock-chip")
                yield Static("[4] Winget", classes="dock-chip")
                yield Static("[5] Read Later", classes="dock-chip")
                yield Static("[6] Evals", classes="dock-chip")
                yield Static("[0/Q] Exit", classes="dock-exit")
            with Horizontal(id="input-row"):
                yield Static("▸ deskpilot run: ", id="prompt-symbol")
                yield Input(
                    placeholder="Type an option [0-6] and press Enter (or press number directly)...",
                    id="prompt-input",
                )

    def on_input_changed(self, event: Input.Changed) -> None:
        """Handle immediate hotkey numbers typed into the input prompt."""
        val = event.value.strip().lower()
        if val in ("0", "q"):
            self.exit(result=0)
        elif val in ("1", "2", "3", "4", "5", "6"):
            self.exit(result=int(val))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle option submitted via Enter in the input line."""
        val = event.value.strip().lower()
        if val in ("0", "q", "exit", "quit"):
            self.exit(result=0)
        elif val in ("1", "2", "3", "4", "5", "6"):
            self.exit(result=int(val))

    def on_key(self, event) -> None:
        """Handle escape and global hotkey navigation."""
        key = event.key.lower()
        if key in ("escape",):
            self.exit(result=0)


async def run_tui_dashboard(
    state: BootState | None = None,
    settings: Settings | None = None,
) -> int:
    """Run the modern Textual full-screen dashboard inside an existing asyncio event loop."""
    app = DeskPilotTUI(state=state, settings=settings)
    result = await app.run_async()
    return result if isinstance(result, int) else 0
