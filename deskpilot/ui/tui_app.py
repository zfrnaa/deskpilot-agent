"""Textual Full-Screen Modern TUI Dashboard for DeskPilot.

Matches the Bento-box visual concept mockup:
- Top Header: Warm Yellow-to-Red gradient stylized ASCII 'DESKPILOT' banner & date/time
- Left Column:
    1. Agent Workflow Pipeline: Rounded pill cards with step connectors
    2. Curated Today News Briefing: Real-time intelligence feed (full 5 headlines)
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
import asyncio
import webbrowser
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from deskpilot.boot_tasks import (
    CalendarAgendaResult,
    TempCleanResult,
    WingetPackageDetail,
    WingetUpdateItem,
    WingetUpdateResult,
    get_winget_package_detail,
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

WingetInspectorModal {
    align: center middle;
}

#inspector-dialog {
    width: 90%;
    height: 85%;
    border: solid #58a6ff;
    background: #161b22;
    padding: 0 1;
    layout: vertical;
}

#inspector-header {
    height: 3;
    border-bottom: solid #30363d;
    padding-top: 1;
}

#inspector-body {
    height: 1fr;
    layout: horizontal;
}

#inspector-left-pane {
    width: 35%;
    height: 1fr;
    border-right: solid #30363d;
    padding-right: 1;
}

#inspector-pkg-list {
    height: 1fr;
    background: #0d1117;
    border: solid #21262d;
}

#inspector-right-pane {
    width: 65%;
    height: 1fr;
    padding-left: 1;
    overflow-y: auto;
}

#inspector-footer {
    height: 3;
    border-top: solid #30363d;
    padding-top: 1;
}

WingetUpgradeRunModal {
    align: center middle;
}

#upgrade-container {
    width: 85%;
    height: 75%;
    border: solid #56d364;
    background: #161b22;
    padding: 1;
    layout: vertical;
}

#upgrade-output {
    height: 1fr;
    background: #0d1117;
    border: solid #21262d;
    overflow-y: auto;
    padding: 1;
}

#upgrade-footer {
    height: 2;
    margin-top: 1;
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


class WingetUpgradeRunModal(ModalScreen[bool]):
    """Live-streaming execution modal for winget package upgrades inside the TUI."""

    def __init__(self, package_id: str | None = None, package_name: str | None = None):
        super().__init__()
        self.package_id = package_id
        self.package_name = package_name or package_id or "All Packages"
        self._is_running = True
        self.success = False

    def compose(self) -> ComposeResult:
        with Vertical(id="upgrade-container"):
            title = f"Upgrading: {self.package_name}" if self.package_id else "Upgrading All Packages"
            yield Static(Text(title, style="bold #56d364"))
            init_msg = "Starting winget upgrade process in background...\n"
            yield Static(Text(init_msg, style="dim"), id="upgrade-output")
            yield Static(Text("Upgrade in progress... Please wait.", style="dim #ffea79"), id="upgrade-footer")

    def on_mount(self) -> None:
        self.run_worker(self._run_upgrade(), exclusive=True)

    async def _run_upgrade(self) -> None:
        cmd = ["winget", "upgrade"]
        if self.package_id:
            cmd.extend(["--id", self.package_id, "--exact"])
        else:
            cmd.extend(["--all"])
        cmd.extend(["--include-unknown", "--accept-source-agreements", "--accept-package-agreements"])

        out_box = self.query_one("#upgrade-output", Static)
        buffer = ""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace")
                buffer += decoded
                out_box.update(buffer)
            await proc.wait()
            self._is_running = False
            self.success = (proc.returncode == 0)
            status = (
                "\n\n[Upgrade completed successfully!]\n"
                if self.success
                else f"\n\n[Upgrade process exited with code {proc.returncode}.]\n"
            )
            buffer += status
            out_box.update(buffer)
        except Exception as e:
            self._is_running = False
            self.success = False
            out_box.update(f"Error executing upgrade: {e}")

        footer = self.query_one("#upgrade-footer", Static)
        footer.update(Text("[Esc / Q] Close and return to Inspector", style="bold #56d364"))

    def on_key(self, event) -> None:
        key = event.key.lower()
        if key in ("escape", "q") and not self._is_running:
            self.dismiss(self.success)


class WingetInspectorModal(ModalScreen[int]):
    """Focused split-pane modal to inspect pending package updates and release notes."""

    def __init__(self, updates: list[WingetUpdateItem]):
        super().__init__()
        self.updates = updates
        self._cache: dict[str, WingetPackageDetail] = {}
        self._current_pkg_id: str = updates[0].id if updates else ""
        self._current_url: str = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="inspector-dialog"):
            header_text = Text()
            header_text.append("Package Update & Changelog Inspector  ", style="bold #58a6ff")
            header_text.append(f"({len(self.updates)} pending updates)", style="dim")
            yield Static(header_text, id="inspector-header")

            with Horizontal(id="inspector-body"):
                with Vertical(id="inspector-left-pane"):
                    yield Static(Text("Select Package (up/down):", style="bold #c9d1d9"))
                    ol = OptionList(id="inspector-pkg-list")
                    for item in self.updates:
                        label = f"{item.name} ({item.version} -> {item.available_version})"
                        ol.add_option(Option(label, id=item.id))
                    yield ol

                with Vertical(id="inspector-right-pane"):
                    yield Static(Text("Loading changelog and package details...", style="dim"), id="inspector-detail-content")

            footer_text = Text()
            footer_text.append("Shortcuts: ", style="bold #8b949e")
            footer_text.append("[U] ", style="bold #ffea79")
            footer_text.append("Upgrade Selected  ", style="dim")
            footer_text.append("[A] ", style="bold #ffea79")
            footer_text.append("Upgrade All  ", style="dim")
            footer_text.append("[O] ", style="bold #58a6ff")
            footer_text.append("Open Changelog URL  ", style="dim")
            footer_text.append("[Esc/Q] ", style="bold #f85149")
            footer_text.append("Back to Dashboard", style="dim")
            yield Static(footer_text, id="inspector-footer")

    def on_mount(self) -> None:
        if self.updates:
            self.run_worker(self._load_package_detail(self.updates[0].id), exclusive=True)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        pkg_id = str(event.option_id)
        self._current_pkg_id = pkg_id
        self.run_worker(self._load_package_detail(pkg_id), exclusive=True)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        # Clicking or pressing Enter selects/inspects the package and keeps focus in TUI
        pkg_id = str(event.option_id)
        self._current_pkg_id = pkg_id
        self.run_worker(self._load_package_detail(pkg_id), exclusive=True)

    async def _load_package_detail(self, pkg_id: str) -> None:
        detail_static = self.query_one("#inspector-detail-content", Static)
        detail_static.update(Text(f"Fetching release notes for {pkg_id}...", style="dim"))

        if pkg_id in self._cache:
            detail = self._cache[pkg_id]
        else:
            detail = await get_winget_package_detail(pkg_id, timeout_secs=8)
            self._cache[pkg_id] = detail

        if self._current_pkg_id != pkg_id:
            return

        self._current_url = detail.release_notes_url or detail.homepage

        pkg_item = next((u for u in self.updates if u.id == pkg_id), None)
        title_str = pkg_item.name if pkg_item else pkg_id
        curr_ver = pkg_item.version if pkg_item else ""
        next_ver = pkg_item.available_version if pkg_item else ""

        txt = Text()
        txt.append(title_str + "\n", style="bold #ffffff")
        txt.append(f"Package ID: {pkg_id}\n", style="dim")
        txt.append(f"Version: {curr_ver} -> ", style="dim")
        txt.append(next_ver + "\n", style="bold #56d364")

        if detail.publisher:
            txt.append(f"Publisher: {detail.publisher}\n", style="dim")
        if detail.homepage:
            txt.append(f"Homepage: {detail.homepage}\n", style="dim #58a6ff")

        txt.append("\n" + ("-" * 50) + "\n\n", style="dim")

        if detail.release_notes:
            txt.append("Release Notes:\n", style="bold #ffea79")
            txt.append(detail.release_notes + "\n\n", style="#c9d1d9")
        elif detail.description:
            txt.append("Description:\n", style="bold #c9d1d9")
            txt.append(detail.description + "\n\n", style="dim")

        if detail.release_notes_url:
            txt.append("Release Notes URL:\n", style="bold #58a6ff")
            txt.append(detail.release_notes_url + "\n", style="underline #58a6ff")
        elif not detail.release_notes and not detail.description:
            if detail.error:
                txt.append(f"Notice: {detail.error}\n", style="dim")
            else:
                txt.append("No inline release notes provided in winget repository for this release.\n", style="dim")

        detail_static.update(txt)

    def handle_upgrade_result(self, upgraded_pkg_id: str | None, success: bool | None) -> None:
        """Remove upgraded package(s) from the inspector list and synchronize app state."""
        if not success:
            return

        ol = self.query_one("#inspector-pkg-list", OptionList)
        detail_static = self.query_one("#inspector-detail-content", Static)
        header_static = self.query_one("#inspector-header", Static)

        if upgraded_pkg_id is None:
            # All packages upgraded
            self.updates.clear()
            ol.clear_options()
            self._current_pkg_id = ""
            detail_static.update(Text("All packages upgraded successfully!\n", style="bold #56d364"))
        else:
            self.updates = [u for u in self.updates if u.id != upgraded_pkg_id]
            try:
                ol.remove_option(upgraded_pkg_id)
            except Exception:
                pass

            if self.updates:
                next_pkg = self.updates[0]
                self._current_pkg_id = next_pkg.id
                self.run_worker(self._load_package_detail(next_pkg.id), exclusive=True)
            else:
                self._current_pkg_id = ""
                detail_static.update(Text("All packages upgraded successfully!\n", style="bold #56d364"))

        # Update modal header count
        header_text = Text()
        header_text.append("Package Update & Changelog Inspector  ", style="bold #58a6ff")
        header_text.append(f"({len(self.updates)} pending updates)", style="dim")
        header_static.update(header_text)

        # Synchronize with main application state and refresh the dashboard card
        if isinstance(self.app, DeskPilotTUI):
            self.app.sync_winget_updates(self.updates)

    def on_key(self, event) -> None:
        key = event.key.lower()
        if key in ("escape", "q"):
            self.dismiss(None)
        elif key in ("u",):
            if not self._current_pkg_id or not self.updates:
                return
            pkg_id = self._current_pkg_id
            pkg_item = next((u for u in self.updates if u.id == pkg_id), None)
            pkg_name = pkg_item.name if pkg_item else pkg_id

            def _on_single_done(success: bool | None) -> None:
                self.handle_upgrade_result(pkg_id, success)

            self.app.push_screen(WingetUpgradeRunModal(package_id=pkg_id, package_name=pkg_name), _on_single_done)
        elif key in ("a",):
            if not self.updates:
                return

            def _on_all_done(success: bool | None) -> None:
                self.handle_upgrade_result(None, success)

            self.app.push_screen(WingetUpgradeRunModal(package_id=None, package_name="All Packages"), _on_all_done)
        elif key in ("o",):
            if self._current_url:
                try:
                    webbrowser.open(self._current_url)
                except Exception:
                    pass


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
                    yield Static("Today Intelligence Briefing (Top Headlines)", classes="card-title")
                    mn = getattr(self.state, "today_news", None)
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
                    yield Static("Package Updates (winget)(Details, press 4)", classes="card-title")
                    wu: WingetUpdateResult | None = self.state.winget_updates
                    yield Static(self._render_winget_text(wu), id="winget-card-content")

        # 3. Bottom Command & Shortcut Dock
        with Vertical(id="dock-container"):
            with Horizontal(id="chips-row"):
                yield Static(Text("Hotkeys:  ", style="dim"))
                yield Static("[1] Screenshots", classes="dock-chip")
                yield Static("[2] Downloads", classes="dock-chip")
                yield Static("[3] Floorp", classes="dock-chip")
                yield Static("[4] Winget", classes="dock-chip")
                yield Static("[5] Read Later", classes="dock-chip")
                yield Static("[6] SystemCare", classes="dock-chip")
                yield Static("[0/Q] Exit", classes="dock-exit")
            with Horizontal(id="input-row"):
                yield Static("▸ deskpilot run: ", id="prompt-symbol")
                yield Input(
                    placeholder="Type an option [0-6] and press Enter (or press number directly)...",
                    id="prompt-input",
                )

    def _render_winget_text(self, wu: WingetUpdateResult | None) -> Text:
        """Render rich styled text representation of package updates."""
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
            for item in wu.updates:
                wu_text.append(f" ▸ {item.name} ", style="bold #ffffff")
                wu_text.append(f"({item.version} → ", style="dim")
                wu_text.append(f"{item.available_version})\n", style="bold #56d364")
        else:
            wu_text.append("● Up to date\n", style="bold #56d364")
            wu_text.append("All packages and system binaries are up to date.\n", style="dim")
        return wu_text

    def sync_winget_updates(self, remaining_updates: list[WingetUpdateItem]) -> None:
        """Update state and re-render dashboard card after packages are upgraded."""
        if isinstance(self.state.winget_updates, WingetUpdateResult):
            self.state.winget_updates.updates = list(remaining_updates)
            self.state.winget_updates.total_count = len(remaining_updates)
        else:
            self.state.winget_updates = WingetUpdateResult(
                total_count=len(remaining_updates),
                updates=list(remaining_updates),
            )
        try:
            st = self.query_one("#winget-card-content", Static)
            st.update(self._render_winget_text(self.state.winget_updates))
        except Exception:
            pass

    def open_winget_inspector(self) -> None:
        """Open the full-screen package updates and changelog modal if updates exist."""
        wu = self.state.winget_updates
        if isinstance(wu, WingetUpdateResult) and wu.updates:
            def _on_modal_dismiss(choice: int | None) -> None:
                if choice is not None:
                    self.exit(result=choice)
                else:
                    try:
                        inp = self.query_one("#prompt-input", Input)
                        inp.value = ""
                        inp.focus()
                    except Exception:
                        pass

            self.push_screen(WingetInspectorModal(wu.updates), _on_modal_dismiss)
        else:
            self.exit(result=4)

    def on_input_changed(self, event: Input.Changed) -> None:
        """Handle immediate hotkey numbers typed into the input prompt."""
        val = event.value.strip().lower()
        if val in ("0", "q"):
            self.exit(result=0)
        elif val == "4":
            self.open_winget_inspector()
        elif val in ("1", "2", "3", "5", "6"):
            self.exit(result=int(val))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle option submitted via Enter in the input line."""
        val = event.value.strip().lower()
        if val in ("0", "q", "exit", "quit"):
            self.exit(result=0)
        elif val == "4":
            self.open_winget_inspector()
        elif val in ("1", "2", "3", "5", "6"):
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
