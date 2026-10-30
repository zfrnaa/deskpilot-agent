"""DeskPilot Rich terminal dashboard and boot orchestrator CLI."""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import langsmith

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from deskpilot.boot_tasks import (
    BookmarkAuditResult,
    CalendarAgendaResult,
    TempCleanResult,
    WingetUpdateResult,
    audit_floorp_bookmarks,
    check_winget_updates,
    clean_temp_directory,
    fetch_today_agenda,
    run_calendar_oauth_flow,
)
from deskpilot.config import Settings, load_settings
from deskpilot.state import BootState, format_bytes
from deskpilot.ui import art as ui_art
from deskpilot.ui import theme as ui_theme
from deskpilot.ui.progress import (
    BOOT_TASK_LABELS,
    animation_enabled,
    boot_animation,
    current_sink,
)


def open_notion_or_browser(page_id: str, web_url: str | None = None) -> bool:
    """Open Notion page in Notion desktop app if available, otherwise open in browser (Chrome preferred)."""
    import os
    import shutil
    import webbrowser

    clean_id = page_id.replace("-", "")
    app_uri = f"notion://www.notion.so/{clean_id}"
    http_url = web_url or f"https://www.notion.so/{clean_id}"

    # Check for Notion desktop app executable or protocol handler
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    notion_exe_path = os.path.join(local_app_data, "Programs", "Notion", "Notion.exe")

    if (local_app_data and os.path.exists(notion_exe_path)) or shutil.which("notion"):
        try:
            # On Windows, os.startfile with the notion:// URI opens directly in Notion app
            if hasattr(os, "startfile"):
                os.startfile(app_uri)
                return True
        except Exception:
            pass

    # Fallback to Chrome or default browser
    try:
        chrome = webbrowser.get("chrome")
        chrome.open(http_url)
        return True
    except Exception:
        return webbrowser.open(http_url)


async def run_phase1_boot_sequence(settings: Settings) -> BootState:
    """Concurrently execute all Phase 1 boot tasks and populate BootState.

    Runs system hygiene, winget updates check,
    and Google Calendar agenda briefing concurrently using asyncio.gather.
    Floorp bookmark audit is decoupled from boot and runs on-demand via option [3].
    """
    state = BootState()

    async def _tracked(label: str, coro: Any) -> Any:
        """Await one boot task, reporting status to the active boot sink if any.

        With no sink installed (as in the test suite) this is a pure
        pass-through: exceptions are re-raised unchanged, so
        gather(return_exceptions=True) keeps producing identical results.
        """
        sink = current_sink()
        if sink is not None:
            sink.begin(label)
        try:
            result = await coro
        except Exception as exc:
            if sink is not None:
                sink.fail(label, str(exc))
            raise
        if sink is not None:
            sink.finish(label)
        return result

    # Execute Phase 1 tasks concurrently via asyncio.gather
    boot_coros: list[Any] = [
        _tracked(BOOT_TASK_LABELS[0], clean_temp_directory(config=settings.temp_cleaner)),
        _tracked(BOOT_TASK_LABELS[1], check_winget_updates(config=settings.winget)),
        _tracked(BOOT_TASK_LABELS[2], fetch_today_agenda(config=settings.calendar)),
    ]

    news_enabled = getattr(settings, "news", None) and settings.news.enabled
    if news_enabled:
        from deskpilot.boot_tasks.news_digest import fetch_morning_news
        boot_coros.append(fetch_morning_news(enabled=True))

    results = await asyncio.gather(*boot_coros, return_exceptions=True)

    if news_enabled:
        temp_res, winget_res, cal_res, news_res = results
        if isinstance(news_res, Exception):
            state.morning_news = None
        else:
            state.morning_news = news_res
    else:
        temp_res, winget_res, cal_res = results
        state.morning_news = None

    # 1. System hygiene
    if isinstance(temp_res, Exception):
        state.add_error(f"System hygiene task failed: {temp_res}")
    else:
        state.system_hygiene = temp_res

    # 2. Winget updates
    if isinstance(winget_res, Exception):
        state.add_error(f"Winget updates check failed: {winget_res}")
    else:
        state.winget_updates = winget_res

    # 3. Google Calendar agenda
    if isinstance(cal_res, Exception):
        state.add_error(f"Calendar briefing failed: {cal_res}")
    else:
        state.calendar_agenda = cal_res

    # Storage Checkpoint Pruning (Morning Boot Hygiene)
    if getattr(settings, "storage", None) and settings.storage.enabled:
        try:
            from deskpilot.storage.maintenance import prune_stale_checkpoints

            await asyncio.to_thread(
                prune_stale_checkpoints,
                db_path=settings.get_resolved_storage_path(),
                max_age_days=settings.storage.checkpoint_retention_days,
            )
        except Exception as exc:
            state.add_error(f"Storage checkpoint pruning failed: {exc}")

    return state


def _render_progress_bar(percent: float, width: int = 14, unicode_ok: bool = True) -> str:
    """Render a sleek mini bar with filled blocks and empty dots or ASCII equivalents."""
    filled_len = max(0, min(width, round(width * (percent / 100.0))))
    empty_len = width - filled_len
    block_char = ui_theme.GLYPH_BLOCK if unicode_ok else "#"
    empty_char = ui_theme.GLYPH_EMPTY_BAR if unicode_ok else "-"
    bar = block_char * filled_len + empty_char * empty_len
    return f"[bold green]{bar}[/bold green]"


def _create_hygiene_panel(state: BootState, unicode_ok: bool = True) -> Panel:
    """Build the Bento-styled Rich panel for System Hygiene (%TEMP% cleaner)."""
    if state.system_hygiene is None or not isinstance(state.system_hygiene, TempCleanResult):
        return ui_theme.card_panel(
            Text("Cleaner not executed or disabled", style="dim"),
            "System Hygiene (%TEMP%)",
            "cyan",
            ui_theme.ICON_HYGIENE,
        )

    sh: TempCleanResult = state.system_hygiene
    freed_str = format_bytes(sh.bytes_freed)

    clean_pct = min(100.0, max(15.0, (sh.bytes_freed / (50 * 1024 * 1024)) * 100.0)) if sh.bytes_freed > 0 else 100.0
    bar_str = _render_progress_bar(clean_pct, width=12, unicode_ok=unicode_ok)
    dot = ui_theme.GLYPH_DOT if unicode_ok else "*"

    sep = "•" if unicode_ok else "|"

    lines: list[str] = [
        f"[dim]Status:[/dim] [bold green]{dot} Cleared[/bold green]  [dim]Storage freed:[/dim] [bold bright_blue]{freed_str}[/bold bright_blue]",
        f"[dim]Hygiene Index:[/dim] {bar_str} [dim]({clean_pct:.0f}%)[/dim]",
        "",
        f"[dim]Items Purged:[/dim] [bold cyan]{sh.files_removed}[/bold cyan] [dim]files[/dim] {sep} [bold cyan]{sh.dirs_removed}[/bold cyan] [dim]dirs[/dim]",
    ]
    if sh.errors:
        lines.append(f"[yellow]Warnings/Errors ({len(sh.errors)}):[/yellow] [dim]{sh.errors[0]}[/dim]")

    return ui_theme.card_panel(
        "\n".join(lines),
        "System Hygiene (%TEMP%)",
        "cyan",
        ui_theme.ICON_HYGIENE,
    )


def _create_bookmarks_panel(state: BootState, unicode_ok: bool = True) -> Panel:
    """Build the Bento-styled Rich panel for Floorp browser bookmarks audit."""
    if state.floorp_bookmarks is None or not isinstance(state.floorp_bookmarks, BookmarkAuditResult):
        return ui_theme.card_panel(
            Text("Audit not executed or disabled", style="dim"),
            "Floorp Bookmarks",
            "magenta",
            ui_theme.ICON_BOOKMARKS,
        )

    fb: BookmarkAuditResult = state.floorp_bookmarks
    if fb.error:
        content = f"[yellow]{fb.error}[/yellow]"
    else:
        dot = ui_theme.GLYPH_DOT if unicode_ok else "*"
        noisy_badge = f"[bold green]0[/bold green]" if fb.noisy_count == 0 else f"[bold yellow]{fb.noisy_count}[/bold yellow]"
        dup_badge = f"[bold green]0[/bold green]" if fb.duplicate_groups_count == 0 else f"[bold yellow]{fb.duplicate_groups_count}[/bold yellow]"
        lines = [
            f"[dim]Audit Status:[/dim] [bold green]{dot} Synced[/bold green]  [dim]Total Bookmarks:[/dim] [bold magenta]{fb.total_bookmarks}[/bold magenta]",
            "",
            f"[dim]Tracking Parameters:[/dim] {noisy_badge} [dim]noisy URLs detected[/dim]",
            f"[dim]Duplicate Clusters:[/dim]  {dup_badge} [dim]duplicate groups[/dim]",
        ]
        content = "\n".join(lines)

    return ui_theme.card_panel(
        content,
        "Floorp Bookmarks",
        "magenta",
        ui_theme.ICON_BOOKMARKS,
    )


def _create_winget_panel(state: BootState, unicode_ok: bool = True) -> Panel:
    """Build the Bento-styled Rich panel for winget package updates."""
    if state.winget_updates is None or not isinstance(state.winget_updates, WingetUpdateResult):
        return ui_theme.card_panel(
            Text("Check not executed or disabled", style="dim"),
            "Package Updates (winget)",
            "blue",
            ui_theme.ICON_WINGET,
        )

    wu: WingetUpdateResult = state.winget_updates
    dot = ui_theme.GLYPH_DOT if unicode_ok else "*"
    arrow = "->" if not unicode_ok else "→"
    bullet = ui_theme.GLYPH_PROMPT if unicode_ok else ">"

    if wu.timed_out:
        content = "[yellow]Query timed out. Run manually if needed.[/yellow]"
    elif wu.error:
        content = f"[yellow]{wu.error}[/yellow]"
    elif wu.total_count == 0:
        content = (
            f"[bold green]{dot} Up to date[/bold green]\n\n"
            "[dim]All local software packages and dependencies are on their latest release.[/dim]"
        )
    else:
        lines = [
            f"[dim]Status:[/dim] [bold yellow]{dot} Updates Pending[/bold yellow]  [dim]Available:[/dim] [bold yellow]{wu.total_count}[/bold yellow] [dim]package(s)[/dim]",
            "",
        ]
        for item in wu.updates[:4]:
            lines.append(f" [cyan]{bullet}[/cyan] [bold]{item.name}[/bold] [dim]({item.version} {arrow} [green]{item.available_version}[/green])[/dim]")
        if len(wu.updates) > 4:
            lines.append(f"   [dim]... and {len(wu.updates) - 4} more updates pending[/dim]")
        content = "\n".join(lines)

    return ui_theme.card_panel(
        content,
        "Package Updates (winget)",
        "blue",
        ui_theme.ICON_WINGET,
    )


def _create_calendar_panel(state: BootState, unicode_ok: bool = True) -> Panel:
    """Build the Bento-styled Rich panel for Google Calendar today's agenda."""
    if state.calendar_agenda is None or not isinstance(state.calendar_agenda, CalendarAgendaResult):
        return ui_theme.card_panel(
            Text("Agenda not fetched or disabled", style="dim"),
            "Today's Agenda (Google Calendar)",
            "green",
            ui_theme.ICON_CALENDAR,
        )

    ca: CalendarAgendaResult = state.calendar_agenda
    dot = ui_theme.GLYPH_DOT if unicode_ok else "*"
    arrow = "->" if not unicode_ok else "→"

    if ca.error:
        content = f"[yellow]{ca.error}[/yellow]"
    elif not ca.is_configured:
        content = "[dim]Calendar not configured (token/credentials missing)[/dim]"
    elif not ca.events:
        content = f"[bold green]{dot} Clear Schedule[/bold green]\n\n[dim]No meetings or events scheduled for today ({ca.date_str or 'today'}).[/dim]"
    else:
        date_header = f"[dim]Date:[/dim] [bold white]{ca.date_str}[/bold white]  [dim]Events:[/dim] [bold green]{ca.total_count}[/bold green]"
        lines = [date_header, ""]
        for ev in ca.events[:5]:
            time_badge = "[bold cyan][All Day][/bold cyan]" if ev.is_all_day else f"[bold cyan]{ev.start_time}[/bold cyan] [dim]{arrow} {ev.end_time}[/dim]"
            loc = f" [dim]({ev.location})[/dim]" if ev.location else ""
            lines.append(f" [green]{dot}[/green] {time_badge}  [bold]{ev.summary}[/bold]{loc}")
        if len(ca.events) > 5:
            lines.append(f"   [dim]... and {len(ca.events) - 5} more upcoming[/dim]")
        content = "\n".join(lines)

    return ui_theme.card_panel(
        content,
        "Today's Agenda (Google Calendar)",
        "green",
        ui_theme.ICON_CALENDAR,
    )


def _create_news_panel(state: BootState, unicode_ok: bool = True) -> Panel:
    """Build the Bento-styled Rich panel for top curated morning news headlines."""
    from deskpilot.boot_tasks.news_digest import NewsDigestResult

    mn = getattr(state, "morning_news", None)
    if mn is None or not isinstance(mn, NewsDigestResult) or not mn.headlines:
        return ui_theme.card_panel(
            Text("No news headlines fetched or offline", style="dim"),
            "Morning Briefing (Top Headlines)",
            "yellow",
            "[NEWS]",
        )

    dot = ui_theme.GLYPH_DOT if unicode_ok else "*"
    diamond = ui_theme.GLYPH_DIAMOND if unicode_ok else "*"

    lines: list[str] = [
        f"[dim]Intelligence Feed:[/dim] [bold yellow]{dot} Live[/bold yellow]  [dim]Curated:[/dim] [bold white]{len(mn.headlines)} stories[/bold white]",
        "",
    ]
    for item in mn.headlines[:5]:
        badge = f"[bold magenta]{item.topic}[/bold magenta]"
        src = f" [dim]via {item.source}[/dim]" if item.source else ""
        link_str = f" [link={item.link}][dim underline]open[/dim underline][/link]" if item.link else ""
        lines.append(f" [yellow]{diamond}[/yellow] [{badge}] [bold]{item.title}[/bold]{src}{link_str}")

    return ui_theme.card_panel(
        "\n".join(lines),
        "Morning Briefing (Top Headlines)",
        "yellow",
        "[NEWS]",
    )


def render_dashboard(
    state: BootState,
    console: Console | None = None,
    settings: Settings | None = None,
) -> None:
    """Render the morning boot terminal dashboard using Rich components."""
    c = ui_theme.get_console(console)

    # Header banner (block-art wordmark, or a single line on narrow terminals)
    time_str = state.timestamp.strftime("%A, %B %d, %Y - %I:%M %p")
    header_panel = ui_art.make_header(
        time_str,
        width=c.width,
        unicode_ok=ui_theme.supports_glyphs(getattr(c, "file", None)),
    )
    c.print(header_panel)

    # Tracing indicator if enabled
    tracing_enabled = False
    project = "DeskPilot"
    url = "https://smith.langchain.com/projects/p/DeskPilot"

    if settings is not None:
        tracing_enabled = bool(settings.langsmith_tracing or settings.langchain_tracing_v2)
        project = settings.langsmith_project or settings.langchain_project or "DeskPilot"
        url = settings.get_langsmith_project_url()
    elif os.getenv("LANGSMITH_TRACING") == "true" or os.getenv("LANGCHAIN_TRACING_V2") == "true":
        tracing_enabled = True
        project = os.getenv("LANGSMITH_PROJECT") or os.getenv("LANGCHAIN_PROJECT") or "DeskPilot"
        url = f"https://smith.langchain.com/projects/p/{project}"

    if tracing_enabled:
        icon = "\U0001F4E1 " if ui_theme.supports_glyphs(getattr(c, "file", None)) else ""
        c.print(
            f"[dim]{icon}LangSmith Tracing: [green]Active[/green] (Project: {project}) [link={url}]{url}[/link][/dim]"
        )

    unicode_ok = ui_theme.supports_glyphs(getattr(c, "file", None))
    hygiene_panel = _create_hygiene_panel(state, unicode_ok=unicode_ok)
    winget_panel = _create_winget_panel(state, unicode_ok=unicode_ok)
    calendar_panel = _create_calendar_panel(state, unicode_ok=unicode_ok)

    # --- Two-column Bento Grid Layout ---
    # Left column: Workflow Pipeline Status + Morning News Feed + (optional) Floorp Bookmarks
    # Right column: System Hygiene + Today's Agenda + Package Updates
    left_table = Table.grid(expand=True, padding=(0, 0))
    right_table = Table.grid(expand=True, padding=(0, 0))

    from deskpilot.ui.pipeline import render_pipeline

    pipeline_card = render_pipeline(
        ["Ingest Watcher", "Vision Triage", "Notion Sync"],
        current_index=0,
        status_msg="Orchestrator ready. Monitoring pending triage queues.",
        unicode_ok=unicode_ok,
        title="Agent Workflow Pipeline",
    )

    left_table.add_row(pipeline_card)
    if getattr(state, "morning_news", None) is not None:
        left_table.add_row(_create_news_panel(state, unicode_ok=unicode_ok))
    if state.bookmarks is not None:
        left_table.add_row(_create_bookmarks_panel(state, unicode_ok=unicode_ok))

    # Right column stack
    right_table.add_row(hygiene_panel)
    right_table.add_row(calendar_panel)
    right_table.add_row(winget_panel)

    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column("left", ratio=5)
    grid.add_column("right", ratio=5)
    grid.add_row(left_table, right_table)

    c.print(grid)

    # Error panel if any errors were recorded during boot
    if state.has_errors():
        err_lines = "\n".join(f"• {err}" for err in state.errors)
        c.print(
            ui_theme.panel(
                err_lines,
                "Boot Sequence Warnings & Errors",
                "red",
                ui_theme.ICON_ERROR,
            )
        )


def normalize_menu_choice(raw: str) -> str:
    """Normalize input choice by stripping whitespace, BOM markers, and brackets."""
    clean = raw.strip().strip("'\"").strip("\ufeff\xef\xbb\xbf\x00 ")
    if clean in {"0", "1", "2", "3", "4", "5", "6"}:
        return clean
    digits = [ch for ch in clean if ch in "0123456"]
    if len(digits) == 1:
        return digits[0]
    return clean


def prompt_action_menu(
    console: Console | None = None,
    prompt_func: Callable[[str], str] = input,
    voice_enabled: bool = True,
) -> str:
    """Display the action menu and prompt user for their selection."""
    c = ui_theme.get_console(console)
    unicode_ok = ui_theme.supports_glyphs(getattr(c, "file", None))

    menu_table = Table(show_header=False, box=None, padding=(0, 1))
    menu_table.add_column("Key", style="bold cyan", width=6)
    menu_table.add_column("Action", style="white")

    # Clean action chips with badges
    badge_active = "[bold green]LIVE[/bold green]" if unicode_ok else "LIVE"
    bullet = "▸" if unicode_ok else ">"

    menu_table.add_row(
        "[bold cyan][1][/bold cyan]",
        f"Triage Screenshots  [dim]• Sort to Notion & cleanup[/dim]",
    )
    menu_table.add_row(
        "[bold cyan][2][/bold cyan]",
        f"Downloads Folder Cleanup  [dim]• Smart categorize & delete advice[/dim]",
    )
    menu_table.add_row(
        "[bold cyan][3][/bold cyan]",
        f"Clean Floorp Bookmarks  [dim]• Launch floorp bookmark preview[/dim]",
    )
    menu_table.add_row(
        "[bold cyan][4][/bold cyan]",
        f"Upgrade Winget Packages  [dim]• Execute interactive winget upgrade[/dim]",
    )
    menu_table.add_row(
        "[bold cyan][5][/bold cyan]",
        f"Notion Read-Later Digest  [dim]• Preview pick & mark read[/dim]",
    )
    menu_table.add_row(
        "[bold cyan][6][/bold cyan]",
        f"Run Agent Evaluations  [dim]• LangSmith Benchmark[/dim]",
    )
    menu_table.add_row(
        "[bold yellow][0][/bold yellow]",
        "[dim]Dismiss & Exit[/dim]",
    )

    c.print(
        ui_theme.card_panel(
            menu_table,
            "Action Menu",
            "green",
            ui_theme.ICON_MENU,
        )
    )
    prompt_glyph = ui_theme.GLYPH_PROMPT if unicode_ok else ">"
    selection = normalize_menu_choice(prompt_func(f"{prompt_glyph} Select an option [0-6]: "))
    if selection in {"0", "1", "2", "3", "4", "5", "6"} and voice_enabled:
        try:
            from deskpilot.voice import play_chime_in_background

            play_chime_in_background("option")
        except Exception:
            pass
    return selection


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for DeskPilot CLI."""
    parser = argparse.ArgumentParser(
        prog="deskpilot",
        description="DeskPilot Morning Command Center and Boot Orchestrator",
    )
    parser.add_argument(
        "--startup",
        action="store_true",
        help="Run in non-interactive startup mode (executes boot sequence, renders dashboard, and exits without waiting for user input)",
    )
    parser.add_argument(
        "--check-tracing",
        action="store_true",
        help="Run LangSmith tracing health check and connectivity diagnostic",
    )
    parser.add_argument(
        "--eval",
        action="store_true",
        help="Run agent evaluation suite and benchmark",
    )
    parser.add_argument(
        "--eval-target",
        default="downloads",
        choices=["downloads"],
        help="Agent target for evaluation",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Run evaluations locally without syncing to LangSmith",
    )
    parser.add_argument(
        "--storage-status",
        action="store_true",
        help="Display SQLite storage and cross-thread memory statistics",
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="Launch the full-screen interactive modern Bento TUI dashboard",
    )
    parsed, _ = parser.parse_known_args(args)
    return parsed


def show_storage_status(settings: Settings, console: Console | None = None) -> None:
    """Display SQLite storage and cross-thread memory diagnostics."""
    c = ui_theme.get_console(console)
    from deskpilot.storage.maintenance import get_storage_stats

    db_path = settings.get_resolved_storage_path()
    stats = get_storage_stats(db_path)

    table = Table(show_header=True, header_style="bold magenta", box=None)
    table.add_column("Property", style="bold cyan")
    table.add_column("Value", style="white")

    table.add_row("Database Path", str(stats["db_path"]))
    table.add_row("File Size", stats["file_size_formatted"])
    table.add_row(
        "Checkpoints",
        f"{stats['checkpoint_count']} active checkpoints",
    )
    table.add_row(
        "Total Long-Term Memories",
        f"{stats['memories_count']} items across {len(stats['namespaces'])} namespace(s)",
    )
    table.add_row(
        "Retention Policy",
        f"Auto-pruning stale checkpoints older than {settings.storage.checkpoint_retention_days} days",
    )

    if stats["namespaces"]:
        table.add_section()
        for ns, count in stats["namespaces"].items():
            unit = "rule" if count == 1 else "rules"
            table.add_row(f"  • {ns}", f"{count} {unit}")

    panel = Panel(
        table,
        title="[bold green]DeskPilot SQLite Storage & Memory Status[/bold green]",
        subtitle="[dim]Dual-Layer Persistence Diagnostics[/dim]",
        border_style="green",
        expand=False,
    )
    c.print(panel)


def check_tracing_health(settings: Settings, console: Console | None = None) -> bool:
    """Check LangSmith tracing configuration and test connectivity to LangSmith API."""
    c = ui_theme.get_console(console)
    is_tracing = bool(settings.langsmith_tracing or settings.langchain_tracing_v2)

    if not is_tracing:
        c.print(
            Panel(
                "[yellow]Tracing is currently disabled.[/yellow]\n"
                "To enable tracing, set [bold]LANGSMITH_TRACING=true[/bold] in your [cyan].env[/cyan] file.",
                title="[bold yellow]LangSmith Tracing: Disabled[/bold yellow]",
                border_style="yellow",
            )
        )
        return False

    api_key = settings.langsmith_api_key or settings.langchain_api_key
    if not api_key:
        c.print(
            Panel(
                "[bold red]LANGSMITH_API_KEY is missing.[/bold red]\n"
                "Tracing is enabled, but no API key was provided.\n"
                "Please configure [bold]LANGSMITH_API_KEY[/bold] in your [cyan].env[/cyan] file.",
                title="[bold red]LangSmith Tracing: Missing API Key[/bold red]",
                border_style="red",
            )
        )
        return False

    project = settings.langsmith_project or settings.langchain_project or "DeskPilot"
    url = settings.get_langsmith_project_url()

    try:
        client = langsmith.Client(api_key=api_key)
        try:
            client.read_project(project_name=project)
        except Exception as read_err:
            if "not found" in str(read_err).lower() or type(read_err).__name__ == "LangSmithNotFoundError":
                pass
            else:
                raise read_err

        storage_line = ""
        if getattr(settings, "storage", None) and settings.storage.enabled:
            try:
                from deskpilot.storage.maintenance import get_storage_stats

                stats = get_storage_stats(settings.get_resolved_storage_path())
                db_disp = str(settings.storage.db_path)
                storage_line = f"\n[bold]Storage:[/bold] {db_disp} (healthy, {stats['memories_count']} memories, {stats['checkpoint_count']} checkpoints)"
            except Exception:
                storage_line = f"\n[bold]Storage:[/bold] {settings.storage.db_path} (unavailable)"

        c.print(
            Panel(
                f"[bold]Status:[/bold] [green]Connected[/green]\n"
                f"[bold]Project:[/bold] {project}\n"
                f"[bold]Dashboard URL:[/bold] [link={url}]{url}[/link]"
                f"{storage_line}",
                title="[bold green]LangSmith Tracing Health[/bold green]",
                border_style="green",
            )
        )
        return True
    except Exception as err:
        c.print(
            Panel(
                f"[bold]Status:[/bold] [red]Connection Failed[/red]\n"
                f"[bold]Project:[/bold] {project}\n"
                f"[bold]Error:[/bold] {err}\n"
                f"[bold]Dashboard URL:[/bold] [link={url}]{url}[/link]",
                title="[bold red]LangSmith Tracing Health Error[/bold red]",
                border_style="red",
            )
        )
        return False


async def execute_menu_action(
    choice: str,
    state: BootState | None = None,
    console: Console | None = None,
    settings: Settings | None = None,
    prompt_func: Callable[[str], str] = input,
) -> bool:
    """Execute the selected menu action.

    Returns True if the menu loop should continue, False if it should exit.
    """
    c = ui_theme.get_console(console)
    normalized_choice = normalize_menu_choice(choice)

    if normalized_choice == "0":
        c.print("[dim]Exiting DeskPilot. Have a productive day![/dim]")
        return False
    elif normalized_choice == "1":
        from deskpilot.ui.pipeline import render_pipeline
        unicode_ok = ui_theme.supports_glyphs(getattr(c, "file", None))
        c.print(
            render_pipeline(
                ["Scan", "Vision Triage", "Cluster", "Review", "Notion Sync", "Cleanup"],
                current_index=0,
                status_msg="Initializing vision model & scanning screenshots directory...",
                unicode_ok=unicode_ok,
                title="Screenshot Triage Agent Pipeline",
            )
        )
        try:
            from deskpilot.agent_tasks.screenshot_agent.graph import run_screenshot_triage
            from deskpilot.agent_tasks.screenshot_agent.vision import (
                check_gemini_quota,
                get_default_vision_llm,
                get_ollama_reasoning_llm,
                get_ollama_vision_llm,
            )

            cfg = settings or load_settings()
            selected_vision_llm = None
            selected_reasoning_llm = None
            fallback_reasoning_llm = None

            # Determine vision model routing (Gemini vs local Ollama minicpm-v)
            if cfg.gemini_api_key:
                c.print("[dim]Checking Gemini API quota / credits...[/dim]")
                has_credits = check_gemini_quota(gemini_api_key=cfg.gemini_api_key, model=cfg.gemini_model)
                if not has_credits:
                    c.print(
                        "[yellow]Gemini credits/quota exhausted or unavailable. "
                        "Automatically routing to local Ollama (minicpm-v)...[/yellow]"
                    )
                    selected_vision_llm = get_ollama_vision_llm(
                        ollama_model=getattr(cfg.ollama, "model", "minicpm-v"),
                        ollama_url=getattr(cfg.ollama, "url", "http://localhost:11434"),
                    )
                    selected_reasoning_llm = get_ollama_reasoning_llm(
                        ollama_model=getattr(cfg.ollama, "reasoning_model", "qwen2.5:3b"),
                        ollama_url=getattr(cfg.ollama, "url", "http://localhost:11434"),
                    )
                    fallback_reasoning_llm = None
                else:
                    ans = (
                        prompt_func(
                            "This will run on Gemini, will you proceed? If no, run it on Ollama [y/n]: "
                        )
                        .strip()
                        .lower()
                    )
                    if ans in {"n", "no"}:
                        c.print("[cyan]Using local Ollama (minicpm-v) as requested.[/cyan]")
                        selected_vision_llm = get_ollama_vision_llm(
                            ollama_model=getattr(cfg.ollama, "model", "minicpm-v"),
                            ollama_url=getattr(cfg.ollama, "url", "http://localhost:11434"),
                        )
                        selected_reasoning_llm = get_ollama_reasoning_llm(
                            ollama_model=getattr(cfg.ollama, "reasoning_model", "qwen2.5:3b"),
                            ollama_url=getattr(cfg.ollama, "url", "http://localhost:11434"),
                        )
                        fallback_reasoning_llm = None
                    else:
                        c.print("[cyan]Proceeding with Gemini Vision model.[/cyan]")
                        selected_vision_llm = get_default_vision_llm(
                            gemini_api_key=cfg.gemini_api_key,
                            model=cfg.gemini_model,
                        )
                        selected_reasoning_llm = get_default_vision_llm(
                            gemini_api_key=cfg.gemini_api_key,
                            model=cfg.gemini_model,
                        )
                        fallback_reasoning_llm = get_ollama_reasoning_llm(
                            ollama_model=getattr(cfg.ollama, "reasoning_model", "qwen2.5:3b"),
                            ollama_url=getattr(cfg.ollama, "url", "http://localhost:11434"),
                        )
            else:
                c.print("[yellow]Gemini API key not configured. Using local Ollama (minicpm-v)...[/yellow]")
                selected_vision_llm = get_ollama_vision_llm(
                    ollama_model=getattr(cfg.ollama, "model", "minicpm-v"),
                    ollama_url=getattr(cfg.ollama, "url", "http://localhost:11434"),
                )
                selected_reasoning_llm = get_ollama_reasoning_llm(
                    ollama_model=getattr(cfg.ollama, "reasoning_model", "qwen2.5:3b"),
                    ollama_url=getattr(cfg.ollama, "url", "http://localhost:11434"),
                )
                fallback_reasoning_llm = None

            triage_res = await run_screenshot_triage(
                cfg,
                llm=selected_vision_llm,
                reasoning_llm=selected_reasoning_llm,
                fallback_reasoning_llm=fallback_reasoning_llm,
                prompt_func=prompt_func,
                console_print=c.print,
            )
            if state is not None:
                state.set_finding("screenshot_triage", triage_res)

            synced = triage_res.get("synced_count", 0)
            deleted = triage_res.get("deleted_count", 0)
            errors = triage_res.get("errors", [])
            c.print(f"[bold green]Screenshot Triage complete:[/bold green] Synced: {synced}, Deleted: {deleted}")
            if any("without passing to Notion" in str(err) for err in errors):
                c.print("[yellow]Finished the task without passing to Notion.[/yellow]")
            if errors:
                c.print(f"[yellow]Warnings/Errors ({len(errors)}):[/yellow]")
                for err in errors:
                    c.print(f"  [yellow]• {err}[/yellow]")

            if cfg.voice.enabled:
                try:
                    from deskpilot.voice import speak_in_background, summarize_screenshots_for_voice

                    speak_in_background(
                        summarize_screenshots_for_voice(synced, len(errors)),
                        cfg.voice,
                    )
                except Exception:
                    pass
        except Exception as e:
            c.print(f"[red]Screenshot Triage failed: {e}[/red]")
        return True
    elif normalized_choice == "2":
        from deskpilot.ui.pipeline import render_pipeline
        unicode_ok = ui_theme.supports_glyphs(getattr(c, "file", None))
        c.print(
            render_pipeline(
                ["Scan Downloads", "Categorize & Dups", "Propose Plan", "User Review", "Execute Actions"],
                current_index=0,
                status_msg="Scanning downloads folder & identifying file categories...",
                unicode_ok=unicode_ok,
                title="Downloads Hygiene Agent Pipeline",
            )
        )
        try:
            from deskpilot.agent_tasks.downloads_agent.graph import run_downloads_hygiene

            cfg = settings or load_settings()
            dh_res = await run_downloads_hygiene(cfg)
            if state is not None:
                state.set_finding("downloads_hygiene", dh_res)

            freed = format_bytes(dh_res.get("total_bytes_freed", 0))
            moved = dh_res.get("total_files_moved", 0)
            errors = dh_res.get("errors", [])
            c.print(f"[bold green]Downloads Cleanup complete:[/bold green] Freed: {freed}, Moved to Archive: {moved}")
            if errors:
                c.print(f"[yellow]Warnings/Errors ({len(errors)}):[/yellow] {errors[0]}")

            if cfg.voice.enabled:
                try:
                    from deskpilot.voice import speak_in_background, summarize_downloads_for_voice

                    speak_in_background(
                        summarize_downloads_for_voice(moved),
                        cfg.voice,
                    )
                except Exception:
                    pass
        except Exception as e:
            c.print(f"[red]Downloads Cleanup failed: {e}[/red]")
        return True
    elif normalized_choice == "3":
        c.print("[cyan]Auditing Floorp Bookmarks...[/cyan]")
        try:
            cfg = settings or load_settings()
            fb_res = await audit_floorp_bookmarks(config=cfg.floorp)
            if state is not None:
                state.floorp_bookmarks = fb_res

            if fb_res.error:
                c.print(f"[yellow]Floorp bookmark audit error: {fb_res.error}[/yellow]")
            else:
                c.print(
                    Panel(
                        f"[bold]Total Bookmarks:[/bold] {fb_res.total_bookmarks}\n"
                        f"[bold]Noisy Tracking Bookmarks:[/bold] {fb_res.noisy_count}\n"
                        f"[bold]Duplicate Groups:[/bold] {fb_res.duplicate_groups_count}\n"
                        f"[bold]Profile Path:[/bold] {fb_res.profile_path or 'Default'}",
                        title="[bold magenta]Floorp Bookmarks Preview[/bold magenta]",
                        border_style="magenta",
                    )
                )
                if fb_res.sample_noisy:
                    noisy_table = Table(
                        title="Sample Noisy Bookmarks (Tracking URLs / Low Quality Titles)",
                        show_header=True,
                    )
                    noisy_table.add_column("Title", style="white", ratio=1)
                    noisy_table.add_column("URL", style="dim", ratio=2)
                    for item in fb_res.sample_noisy[:5]:
                        noisy_table.add_row(item.get("title", "")[:40], item.get("url", "")[:60])
                    c.print(noisy_table)

                if fb_res.sample_duplicates:
                    dup_table = Table(title="Sample Duplicate Bookmark Groups", show_header=True)
                    dup_table.add_column("Count", style="cyan", width=8)
                    dup_table.add_column("Normalized URL", style="dim", ratio=1)
                    for item in fb_res.sample_duplicates[:5]:
                        dup_table.add_row(str(item.get("count", 0)), item.get("url", "")[:70])
                    c.print(dup_table)

                if cfg.voice.enabled:
                    try:
                        from deskpilot.voice import speak_in_background, summarize_bookmarks_for_voice

                        speak_in_background(
                            summarize_bookmarks_for_voice(fb_res),
                            cfg.voice,
                        )
                    except Exception:
                        pass
        except Exception as e:
            c.print(f"[red]Floorp bookmark audit failed: {e}[/red]")
        return True
    elif normalized_choice == "4":
        c.print("[cyan]Checking for package updates...[/cyan]")
        try:
            cfg = settings or load_settings()
            if cfg.winget.ignore_packages:
                c.print(
                    f"[yellow]Ignored packages excluded from reporting: {', '.join(cfg.winget.ignore_packages)}[/yellow]"
                )

            # Check for updates or use already fetched updates from state
            updates = None
            if state is not None and isinstance(state.winget_updates, WingetUpdateResult) and state.winget_updates.updates:
                updates = state.winget_updates.updates
            else:
                wu_res = await check_winget_updates(config=cfg.winget)
                if wu_res.error:
                    c.print(f"[yellow]Package check warning: {wu_res.error}[/yellow]")
                elif wu_res.timed_out:
                    c.print("[yellow]Package check timed out.[/yellow]")
                elif wu_res.total_count == 0:
                    c.print("[bold green]All packages up to date! Nothing to upgrade.[/bold green]")
                    return True
                else:
                    updates = wu_res.updates

            if updates:
                pkg_table = Table(title=f"Available Updates ({len(updates)})", show_header=True)
                pkg_table.add_column("Package Name", style="white", ratio=2)
                pkg_table.add_column("Package Id", style="cyan", ratio=2)
                pkg_table.add_column("Current Version", style="dim", ratio=1)
                pkg_table.add_column("Available Version", style="green", ratio=1)
                for item in updates:
                    pkg_table.add_row(item.name, item.id, item.version, item.available_version)
                c.print(pkg_table)

                ans = (
                    prompt_func("Do you want to proceed with upgrading packages? [y/N]: ")
                    .strip()
                    .lower()
                )
                if ans not in {"y", "yes"}:
                    c.print("[dim]Package upgrade cancelled by user.[/dim]")
                    return True

            c.print("[cyan]Executing interactive winget upgrade...[/cyan]")
            subprocess.run(["winget", "upgrade", "--all", "--include-unknown"], check=False)
        except FileNotFoundError:
            c.print("[red]winget executable not found on this system.[/red]")
        except Exception as e:
            c.print(f"[red]Failed to run winget upgrade: {e}[/red]")
        return True
    elif normalized_choice == "5":
        c.print("[cyan]Starting Notion Read-Later Digest Agent...[/cyan]")
        try:
            from deskpilot.agent_tasks.read_later_agent.graph import run_read_later_flow
            from deskpilot.agent_tasks.read_later_agent.notion_reader import update_item_status

            cfg = settings or load_settings()
            rl_res = await run_read_later_flow(cfg)
            if state is not None:
                state.set_finding("read_later_digest", rl_res)

            unread = len(rl_res.get("unread_items", []))
            rec = rl_res.get("recommended_item")
            rec_title = getattr(rec, "title", str(rec)) if rec else "None"
            errors = rl_res.get("errors", [])
            c.print(f"[bold green]Read-Later Digest complete:[/bold green] Unread: {unread}, Recommended: {rec_title}")
            if errors:
                c.print(f"[yellow]Warnings/Errors ({len(errors)}):[/yellow] {errors[0]}")

            if rec is not None:
                lines = [
                    f"[bold cyan]Title:[/bold cyan] {getattr(rec, 'title', 'Untitled')}",
                    f"[bold]URL:[/bold] {getattr(rec, 'url', None) or 'N/A'}",
                    f"[bold]Added:[/bold] {getattr(rec, 'added_date', None) or 'N/A'}",
                    f"[bold]Tags:[/bold] {', '.join(getattr(rec, 'tags', [])) if getattr(rec, 'tags', None) else 'None'}",
                ]
                c.print(
                    Panel(
                        "\n".join(lines),
                        title="[bold green]Recommended Item to Read[/bold green]",
                        border_style="green",
                    )
                )

                ans = prompt_func("\nDo you want to open and read this item now? [Y/n]: ").strip().lower()
                if ans in ("", "y", "yes", "r", "read"):
                    c.print(f"[cyan]Opening '{rec.title}'...[/cyan]")
                    opened = open_notion_or_browser(page_id=rec.page_id, web_url=rec.url)
                    if not opened:
                        c.print("[yellow]Could not launch Notion app or browser.[/yellow]")

                    mark_ans = prompt_func("\nMark this item as 'Read' in Notion? [Y/n]: ").strip().lower()
                    if mark_ans in ("", "y", "yes", "r", "read"):
                        token = cfg.notion.token
                        if token:
                            from notion_client import Client
                            cl = Client(auth=token)
                            updated = await update_item_status(page_id=rec.page_id, new_status="Read", client=cl)
                            if not updated:
                                updated = await update_item_status(page_id=rec.page_id, new_status="read", client=cl)
                            if updated:
                                c.print(f"[bold green]Successfully marked '{rec.title}' as Read in Notion![/bold green]")
                            else:
                                c.print(f"[yellow]Could not update status for '{rec.title}' in Notion.[/yellow]")
        except Exception as e:
            c.print(f"[red]Read-Later Digest failed: {e}[/red]")
        return True
    elif normalized_choice == "6":
        c.print("[cyan]Starting Agent Evaluations (LangSmith Benchmark)...[/cyan]")
        try:
            from deskpilot.evals.runner import run_downloads_evaluation

            cfg = settings or load_settings()
            api_key = cfg.langsmith_api_key or cfg.langchain_api_key
            if api_key:
                c.print("[cyan]Running evaluation and logging to LangSmith...[/cyan]")
                client = langsmith.Client(api_key=api_key)
                run_downloads_evaluation(client=client, local_only=False)
            else:
                c.print("[cyan]Running evaluation in local-only benchmark mode...[/cyan]")
                run_downloads_evaluation(local_only=True)
        except Exception as e:
            c.print(f"[red]Agent Evaluations failed: {e}[/red]")
        return True
    else:
        c.print(f"[bold red]Invalid option '{choice}'. Please select an option between 0 and 6.[/bold red]")
        return True


async def main_async(
    console: Console | None = None,
    settings: Settings | None = None,
    prompt_func: Callable[[str], str] = input,
    startup: bool = False,
    check_tracing: bool = False,
    run_eval: bool = False,
    eval_target: str = "downloads",
    local_only: bool = False,
    storage_status: bool = False,
    use_tui: bool = False,
) -> int:
    """Asynchronous entry point for the DeskPilot morning boot orchestrator."""
    c = ui_theme.get_console(console)
    if settings is None:
        try:
            settings = load_settings()
        except Exception as e:
            c.print(f"[red]Error loading configuration: {e}[/red]")
            return 1

    if storage_status:
        show_storage_status(settings, console=c)
        return 0

    if check_tracing:
        healthy = check_tracing_health(settings, console=c)
        return 0 if healthy else 1

    if run_eval:
        try:
            from deskpilot.evals.runner import run_downloads_evaluation

            api_key = settings.langsmith_api_key or settings.langchain_api_key
            if not local_only and api_key:
                c.print(f"[cyan]Running {eval_target} evaluation against LangSmith...[/cyan]")
                client = langsmith.Client(api_key=api_key)
                run_downloads_evaluation(client=client, local_only=False)
            else:
                c.print(f"[cyan]Running {eval_target} evaluation in local-only benchmark mode...[/cyan]")
                run_downloads_evaluation(local_only=True)
            return 0
        except Exception as e:
            c.print(f"[red]Evaluation run failed: {e}[/red]")
            return 1

    # Initialize LangSmith / LangChain tracing if configured
    settings.setup_tracing()

    # Run Phase 1 boot sequence concurrently, animated on a real terminal only.
    animate = animation_enabled(c, startup)
    with boot_animation(c, enabled=animate, labels=BOOT_TASK_LABELS):
        state = await run_phase1_boot_sequence(settings)

    # Render terminal dashboard
    render_dashboard(state, console=c, settings=settings)

    # Conversational voice briefing dispatch
    voice_worker: Any = None
    if settings.voice.enabled:
        try:
            from deskpilot.voice import speak_in_background, summarize_boot_for_voice

            speech_text = summarize_boot_for_voice(state)
            voice_worker = speak_in_background(speech_text, settings.voice)
        except Exception:
            pass

    if startup:
        c.print("[dim]Startup mode active: boot sequence completed successfully.[/dim]")
        try:
            from deskpilot.ui.notifications import format_boot_notification, send_windows_toast
            title, body = format_boot_notification(state)
            send_windows_toast(title, body, action_command="deskpilot://open")
        except Exception:
            pass

        # In startup mode, wait for the background voice briefing to finish before process termination
        if voice_worker is not None and getattr(voice_worker, "is_alive", None) and voice_worker.is_alive():
            voice_worker.join(timeout=20.0)
        return 0


    # If calendar token is unconfigured or failed authorization/refresh, prompt user to re-authorize
    cal_res = state.calendar_agenda
    if (
        isinstance(cal_res, CalendarAgendaResult)
        and (not cal_res.is_configured or cal_res.error)
        and settings.calendar.enabled
        and settings.calendar.get_resolved_credentials_path().exists()
    ):
        prompt_glyph = ui_theme.GLYPH_PROMPT if ui_theme.supports_glyphs() else ">"
        reauth_ans = prompt_func(
            f"{prompt_glyph} Google Calendar token is expired or not authorized. Would you like to re-authorize now in your browser? [Y/n]: "
        ).strip().lower()
        if reauth_ans in ("", "y", "yes"):
            c.print("[cyan]Opening browser for Google Calendar authorization...[/cyan]")
            try:
                await asyncio.to_thread(run_calendar_oauth_flow, settings.calendar)
                c.print("[green]Re-authorization successful! Fetching today's agenda...[/green]")
                new_cal_res = await fetch_today_agenda(config=settings.calendar)
                state.calendar_agenda = new_cal_res
                render_dashboard(state, console=c, settings=settings)
            except Exception as auth_exc:
                c.print(f"[red]Google Calendar authorization failed: {auth_exc}[/red]")

    # Interactive menu loop
    voice_active = getattr(settings, "voice", None) is None or settings.voice.enabled
    while True:
        try:
            if use_tui and not startup:
                from deskpilot.ui.tui_app import run_tui_dashboard

                tui_choice = run_tui_dashboard(state=state, settings=settings)
                choice = str(tui_choice)
            else:
                choice = prompt_action_menu(console=c, prompt_func=prompt_func, voice_enabled=voice_active)
        except (KeyboardInterrupt, EOFError):
            c.print("\n[dim]Dismissed. Exiting DeskPilot.[/dim]")
            return 0

        should_continue = await execute_menu_action(
            choice,
            state=state,
            console=c,
            settings=settings,
            prompt_func=prompt_func,
        )
        if not should_continue:
            return 0


def _configure_stdio() -> None:
    """Force UTF-8 stdio so dashboard glyphs cannot crash redirected output.

    Windows falls back to the ANSI code page (cp1252) whenever stdout is not a
    console - piped output, a log file, or the logon task. The dashboard uses
    box-drawing, block art and emoji that cp1252 cannot encode, which would
    otherwise raise UnicodeEncodeError mid-boot. ``errors="replace"`` degrades
    an unencodable glyph instead of aborting the run.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main(args: list[str] | None = None) -> None:
    """DeskPilot CLI entry point."""
    _configure_stdio()
    parsed = parse_args(args if args is not None else sys.argv[1:])
    try:
        if parsed.storage_status:
            sys.exit(asyncio.run(main_async(storage_status=True)))
        elif parsed.check_tracing:
            sys.exit(asyncio.run(main_async(check_tracing=True)))
        elif parsed.eval:
            sys.exit(
                asyncio.run(
                    main_async(
                        run_eval=True,
                        eval_target=parsed.eval_target,
                        local_only=parsed.local_only,
                    )
                )
            )
        else:
            if getattr(parsed, "tui", False):
                sys.exit(asyncio.run(main_async(startup=parsed.startup, use_tui=True)))
            else:
                sys.exit(asyncio.run(main_async(startup=parsed.startup)))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
