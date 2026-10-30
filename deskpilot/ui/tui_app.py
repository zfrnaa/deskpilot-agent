"""Textual Full-Screen Modern TUI Dashboard for DeskPilot.

Matches the Bento-box visual concept mockup:
- Top Header: Warm Yellow-to-Red gradient stylized ASCII 'DESKPILOT' banner & date/time
- Left Column:
    1. Agent Workflow Pipeline: Rounded pill cards with step connectors
    2. Curated Morning News Briefing: Real-time intelligence feed (full 5 headlines)
- Right Column:
    1. System Hygiene & Storage Metrics: Fixed height drive space graph & stats
    2. Daily Digest & Calendar: Timeline markers with priority chips
    3. Package Updates (winget): Always visible with dedicated vertical share
- Bottom Command Dock: Interactive hotkey bar [1] Triage [2] Clean [3] Floorp [4] Winget [5] Notion [0] Exit
"""

from __future__ import annotations

import shutil
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
from deskpilot.ui.theme import supports_glyphs


DASHBOARD_CSS = """
Screen {
    background: #0d1117;
    color: #c9d1d9;
    layout: vertical;
    padding: 0 1;
}

#top-header {
    height: auto;
    margin-bottom: 1;
    layout: horizontal;
    align: left middle;
}

#logo-block {
    width: auto;
}

#meta-block {
    width: auto;
    content-align: right middle;
    color: #8b949e;
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
    padding: 0 1;
    margin-bottom: 1;
}

.bento-card:focus {
    border: round #f69d50;
}

.card-title {
    color: #ffea79;
    text-style: bold;
    margin-bottom: 0;
}

#pipeline-card {
    height: 5;
}

#news-card {
    height: 1fr;
    overflow-y: auto;
}

#hygiene-card {
    height: 7;
}

#calendar-card {
    height: 6;
}

#winget-card {
    height: 1fr;
    overflow-y: auto;
}

#dock-container {
    height: 4;
    border-top: solid #30363d;
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
    color: #ffea79;
    text-style: bold;
}

.dock-exit {
    color: #f85149;
    text-style: bold;
}

#input-row {
    height: 2;
    layout: horizontal;
    align: left middle;
}

#prompt-symbol {
    width: auto;
    color: #f69d50;
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


def render_logo_art() -> Text:
    """Render stylized ASCII wordmark banner with a seamless, solid horizontal yellow-to-red sunset flame gradient."""
    logo_lines = [
        r"  ____  _____ ____  _  ______ ___ _     ___ _____ ",
        r" |  _ \| ____/ ___|| |/ /  _ \_ _| |   / _ \_   _|",
        r" | | | |  _| \___ \| ' /| |_) | || |  | | | || |  ",
        r" | |_| | |___ ___) | . \|  __/| || |__| |_| || |  ",
        r" |____/|_____|____/|_|\_\_|  |___|_____\___/ |_|  ",
    ]

    def _hex_to_rgb(h: str) -> tuple[int, int, int]:
        h = h.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    def _interpolate(c1: tuple[int, int, int], c2: tuple[int, int, int], factor: float) -> str:
        r = int(c1[0] + (c2[0] - c1[0]) * factor)
        g = int(c1[1] + (c2[1] - c1[1]) * factor)
        b = int(c1[2] + (c2[2] - c1[2]) * factor)
        return f"#{r:02x}{g:02x}{b:02x}"

    start_rgb = _hex_to_rgb("#ffea79")  # Bright warm golden yellow
    end_rgb = _hex_to_rgb("#f85149")    # Vivid sunset coral red
    max_len = max(len(l) for l in logo_lines)

    banner = Text()
    for row_idx, line in enumerate(logo_lines):
        for col_idx, char in enumerate(line):
            factor = col_idx / max(1, max_len - 1)
            char_color = _interpolate(start_rgb, end_rgb, factor)
            banner.append(char, style=f"bold {char_color}")
        if row_idx < len(logo_lines) - 1:
            banner.append("\n")
    return banner


def render_arc_gauge(freed_str: str) -> Text:
    """Render the compact circular arc gauge matching the sunset flame concept."""
    t = Text()
    t.append(" ╭──────╮\n", style="bold #ffea79")
    t.append(" │", style="bold #ffea79")
    t.append(f"{freed_str:^6}", style="bold #ffffff")
    t.append("│\n", style="bold #f69d50")
    t.append(" ╰Freed─╯", style="bold #f85149")
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

        # 1. Warm Yellow-to-Red Gradient DeskPilot Header + Metadata
        with Horizontal(id="top-header"):
            yield Static(render_logo_art(), id="logo-block")
            meta_text = Text()
            meta_text.append("\n  Dashboard Center\n", style="bold #ffea79")
            meta_text.append(f"  {time_str}\n", style="dim")
            meta_text.append("  Autonomous Local Multi-Agent Orchestrator", style="dim italic")
            yield Static(meta_text, id="meta-block")

        # 2. Main Two-Column Bento Grid
        with Horizontal(id="bento-container"):
            # Left Column (Workflow Pipeline + News Feed)
            with Vertical(id="left-column"):
                with Container(classes="bento-card", id="pipeline-card"):
                    yield Static("Agent Workflow Pipeline", classes="card-title")
                    pipe_text = Text()
                    pipe_text.append("[ ● Ingest Watcher ]", style="bold #56d364")
                    pipe_text.append(" ──→ ", style="bold #f69d50")
                    pipe_text.append("[ ◎ Vision Triage ]", style="bold #ffea79")
                    pipe_text.append(" ──→ ", style="dim")
                    pipe_text.append("[ ○ Notion Sync ]", style="dim")
                    pipe_text.append("\n▸ Orchestrator active. Awaiting manual trigger or scheduled queue.", style="dim")
                    yield Static(pipe_text)

                with Container(classes="bento-card", id="news-card"):
                    yield Static("Morning Intelligence Briefing (Top Headlines)", classes="card-title")
                    mn = getattr(self.state, "morning_news", None)
                    news_text = Text()
                    if mn and isinstance(mn, NewsDigestResult) and mn.headlines:
                        for item in mn.headlines[:5]:
                            news_text.append(" ◆ ", style="bold #ffea79")
                            news_text.append(f"[{item.topic}] ", style="bold #f69d50")
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
                    yield Static("System Hygiene & Storage Metrics", classes="card-title")
                    sh: TempCleanResult | None = self.state.system_hygiene

                    # Calculate system drive metrics
                    try:
                        total_bytes, used_bytes, free_bytes = shutil.disk_usage(".")
                        pct_used = int((used_bytes / total_bytes) * 100) if total_bytes > 0 else 0
                        free_str = format_bytes(free_bytes)
                        total_str = format_bytes(total_bytes)
                    except Exception:
                        pct_used = 0
                        free_str = "N/A"
                        total_str = "N/A"

                    # Determine Unicode glyph vs ASCII fallback for progress bar
                    use_glyphs = supports_glyphs()
                    fill_char = "█" if use_glyphs else "#"
                    empty_char = "░" if use_glyphs else "-"
                    bar_w = 14
                    filled_count = int(round((pct_used / 100.0) * bar_w))
                    filled_count = max(0, min(bar_w, filled_count))
                    bar_str = fill_char * filled_count + empty_char * (bar_w - filled_count)

                    hyg_table = Text()
                    if sh is not None and isinstance(sh, TempCleanResult):
                        freed = format_bytes(sh.bytes_freed)
                        hyg_table.append("Status: ", style="dim")
                        hyg_table.append("● Cleared\n", style="bold #56d364")
                        hyg_table.append(f"Cleaned: {sh.files_removed} files • {sh.dirs_removed} dirs ({freed})\n", style="dim")
                        gauge_freed = freed
                    else:
                        hyg_table.append("Status: ", style="dim")
                        hyg_table.append("○ Not executed / Disabled\n", style="dim")
                        hyg_table.append("Cleaned: 0 files • 0 dirs (0 B)\n", style="dim")
                        gauge_freed = "0 B"

                    # Storage Space Gauge Graph
                    hyg_table.append("Disk:   ", style="dim")
                    hyg_table.append(f"[{bar_str}] ", style="bold #f69d50")
                    bar_color = "#56d364" if pct_used < 75 else ("#ffea79" if pct_used < 90 else "#f85149")
                    hyg_table.append(f"{pct_used}% used\n", style=f"bold {bar_color}")
                    hyg_table.append("Space:  ", style="dim")
                    hyg_table.append(f"{free_str} free ", style="bold #56d364")
                    hyg_table.append(f"of {total_str}", style="dim")

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
                        cal_text.append("● Token unconfigured / Auth required\n", style="bold #ffea79")
                        cal_text.append("Re-authorize in browser via settings.\n", style="dim")
                    elif ca.events:
                        cal_text.append(f"Timeline Schedule ({ca.date_str or 'Today'}):\n", style="dim")
                        for ev in ca.events[:3]:
                            time_chip = f"[{ev.start_time} - {ev.end_time}]" if not ev.is_all_day else "[All Day]"
                            cal_text.append(f" • {time_chip} ", style="bold #f69d50")
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
                        wu_text.append("● Query timed out\n", style="bold #ffea79")
                        wu_text.append("Run winget upgrade manually.\n", style="dim")
                    elif wu.error:
                        wu_text.append("● Check failed: ", style="bold #f85149")
                        wu_text.append(f"{wu.error}\n", style="dim")
                    elif wu.total_count > 0:
                        wu_text.append(f"● {wu.total_count} Updates Pending\n", style="bold #ffea79")
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
