"""DeskPilot Rich terminal dashboard and boot orchestrator CLI."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Callable
from typing import Any

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
)
from deskpilot.config import Settings, load_settings
from deskpilot.state import BootState


def format_bytes(num_bytes: int) -> str:
    """Format bytes count into a human-readable string."""
    val = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(val) < 1024.0:
            return f"{val:.1f} {unit}" if unit != "B" else f"{int(val)} B"
        val /= 1024.0
    return f"{val:.1f} PB"


async def run_phase1_boot_sequence(settings: Settings) -> BootState:
    """Concurrently execute all Phase 1 boot tasks and populate BootState.

    Runs system hygiene, Floorp bookmark audit, winget updates check,
    and Google Calendar agenda briefing concurrently using asyncio.gather.
    """
    state = BootState()

    results = await asyncio.gather(
        clean_temp_directory(config=settings.temp_cleaner),
        audit_floorp_bookmarks(config=settings.floorp),
        check_winget_updates(config=settings.winget),
        fetch_today_agenda(config=settings.calendar),
        return_exceptions=True,
    )

    temp_res, floorp_res, winget_res, cal_res = results

    # 1. System hygiene
    if isinstance(temp_res, Exception):
        state.add_error(f"System hygiene task failed: {temp_res}")
    else:
        state.system_hygiene = temp_res

    # 2. Floorp bookmarks
    if isinstance(floorp_res, Exception):
        state.add_error(f"Floorp bookmark audit failed: {floorp_res}")
    else:
        state.floorp_bookmarks = floorp_res

    # 3. Winget updates
    if isinstance(winget_res, Exception):
        state.add_error(f"Winget updates check failed: {winget_res}")
    else:
        state.winget_updates = winget_res

    # 4. Google Calendar agenda
    if isinstance(cal_res, Exception):
        state.add_error(f"Calendar briefing failed: {cal_res}")
    else:
        state.calendar_agenda = cal_res

    return state


def _create_hygiene_panel(state: BootState) -> Panel:
    """Build the Rich panel for System Hygiene (%TEMP% cleaner)."""
    if state.system_hygiene is None or not isinstance(state.system_hygiene, TempCleanResult):
        return Panel(
            Text("Cleaner not executed or disabled", style="dim"),
            title="[bold cyan]System Hygiene (%TEMP%)[/bold cyan]",
            border_style="cyan",
        )

    sh: TempCleanResult = state.system_hygiene
    lines: list[str] = [
        f"[bold]Bytes Freed:[/bold] [green]{format_bytes(sh.bytes_freed)}[/green]",
        f"[bold]Files Removed:[/bold] {sh.files_removed}",
        f"[bold]Dirs Removed:[/bold] {sh.dirs_removed}",
    ]
    if sh.errors:
        lines.append(f"[yellow]Warnings/Errors ({len(sh.errors)}):[/yellow] {sh.errors[0]}")

    return Panel(
        "\n".join(lines),
        title="[bold cyan]System Hygiene (%TEMP%)[/bold cyan]",
        border_style="cyan",
    )


def _create_bookmarks_panel(state: BootState) -> Panel:
    """Build the Rich panel for Floorp browser bookmarks audit."""
    if state.floorp_bookmarks is None or not isinstance(state.floorp_bookmarks, BookmarkAuditResult):
        return Panel(
            Text("Audit not executed or disabled", style="dim"),
            title="[bold magenta]Floorp Bookmarks[/bold magenta]",
            border_style="magenta",
        )

    fb: BookmarkAuditResult = state.floorp_bookmarks
    if fb.error:
        content = f"[yellow]{fb.error}[/yellow]"
    else:
        noisy_style = "yellow" if fb.noisy_count > 0 else "green"
        dup_style = "yellow" if fb.duplicate_groups_count > 0 else "green"
        lines = [
            f"[bold]Total Bookmarks:[/bold] {fb.total_bookmarks}",
            f"[bold]Noisy URLs (Tracking):[/bold] [{noisy_style}]{fb.noisy_count}[/{noisy_style}]",
            f"[bold]Duplicate Groups:[/bold] [{dup_style}]{fb.duplicate_groups_count}[/{dup_style}]",
        ]
        content = "\n".join(lines)

    return Panel(
        content,
        title="[bold magenta]Floorp Bookmarks[/bold magenta]",
        border_style="magenta",
    )


def _create_winget_panel(state: BootState) -> Panel:
    """Build the Rich panel for winget package updates."""
    if state.winget_updates is None or not isinstance(state.winget_updates, WingetUpdateResult):
        return Panel(
            Text("Check not executed or disabled", style="dim"),
            title="[bold blue]Package Updates (winget)[/bold blue]",
            border_style="blue",
        )

    wu: WingetUpdateResult = state.winget_updates
    if wu.timed_out:
        content = "[yellow]Query timed out. Run manually if needed.[/yellow]"
    elif wu.error:
        content = f"[yellow]{wu.error}[/yellow]"
    elif wu.total_count == 0:
        content = "[bold green]All packages up to date![/bold green]"
    else:
        lines = [f"[bold yellow]{wu.total_count} update(s) available:[/bold yellow]"]
        for item in wu.updates[:5]:
            lines.append(f"• {item.name} ([dim]{item.version}[/dim] -> [cyan]{item.available_version}[/cyan])")
        if len(wu.updates) > 5:
            lines.append(f"[dim]... and {len(wu.updates) - 5} more[/dim]")
        content = "\n".join(lines)

    return Panel(
        content,
        title="[bold blue]Package Updates (winget)[/bold blue]",
        border_style="blue",
    )


def _create_calendar_panel(state: BootState) -> Panel:
    """Build the Rich panel for Google Calendar today's agenda."""
    if state.calendar_agenda is None or not isinstance(state.calendar_agenda, CalendarAgendaResult):
        return Panel(
            Text("Agenda not fetched or disabled", style="dim"),
            title="[bold green]Today's Agenda (Google Calendar)[/bold green]",
            border_style="green",
        )

    ca: CalendarAgendaResult = state.calendar_agenda
    if not ca.is_configured:
        content = "[dim]Calendar not configured (token/credentials missing)[/dim]"
    elif ca.error:
        content = f"[yellow]{ca.error}[/yellow]"
    elif not ca.events:
        content = f"[green]No events scheduled for today ({ca.date_str or 'today'})[/green]"
    else:
        lines = [f"[bold]{ca.total_count} event(s) scheduled ({ca.date_str}):[/bold]"]
        for ev in ca.events[:5]:
            time_badge = "[All Day]" if ev.is_all_day else f"[{ev.start_time} - {ev.end_time}]"
            loc = f" ({ev.location})" if ev.location else ""
            lines.append(f"• [cyan]{time_badge}[/cyan] {ev.summary}{loc}")
        if len(ca.events) > 5:
            lines.append(f"[dim]... and {len(ca.events) - 5} more[/dim]")
        content = "\n".join(lines)

    return Panel(
        content,
        title="[bold green]Today's Agenda (Google Calendar)[/bold green]",
        border_style="green",
    )


def render_dashboard(state: BootState, console: Console | None = None) -> None:
    """Render the morning boot terminal dashboard using Rich components."""
    c = console or Console()

    # Header banner
    time_str = state.timestamp.strftime("%A, %B %d, %Y - %I:%M %p")
    header_panel = Panel(
        Text("DeskPilot Morning Command Center", justify="center", style="bold white"),
        subtitle=time_str,
        border_style="bright_blue",
    )
    c.print(header_panel)

    # 2x2 grid of boot task panels
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)

    hygiene_panel = _create_hygiene_panel(state)
    bookmarks_panel = _create_bookmarks_panel(state)
    winget_panel = _create_winget_panel(state)
    calendar_panel = _create_calendar_panel(state)

    grid.add_row(hygiene_panel, bookmarks_panel)
    grid.add_row(winget_panel, calendar_panel)
    c.print(grid)

    # Error panel if any errors were recorded during boot
    if state.has_errors():
        err_lines = "\n".join(f"• {err}" for err in state.errors)
        c.print(
            Panel(
                err_lines,
                title="[bold red]Boot Sequence Warnings & Errors[/bold red]",
                border_style="red",
            )
        )


def normalize_menu_choice(raw: str) -> str:
    """Normalize input choice by stripping whitespace, BOM markers, and brackets."""
    clean = raw.strip().strip("'\"").strip("\ufeff\xef\xbb\xbf\x00 ")
    if clean in {"0", "1", "2", "3", "4"}:
        return clean
    digits = [ch for ch in clean if ch in "01234"]
    if len(digits) == 1:
        return digits[0]
    return clean


def prompt_action_menu(
    console: Console | None = None,
    prompt_func: Callable[[str], str] = input,
) -> str:
    """Display the action menu and prompt user for their selection."""
    c = console or Console()

    menu_table = Table(show_header=False, box=None, padding=(0, 1))
    menu_table.add_column("Key", style="bold cyan", width=4)
    menu_table.add_column("Action", style="white")

    menu_table.add_row("[1]", "Triage Screenshots (Sort to Notion & cleanup)")
    menu_table.add_row("[2]", "Downloads Folder Cleanup (Smart categorize & delete advice)")
    menu_table.add_row("[3]", "Clean Floorp Bookmarks (Launch floorp bookmark tool or stub)")
    menu_table.add_row("[4]", "Upgrade Winget Packages (Execute interactive winget upgrade)")
    menu_table.add_row("[0]", "Dismiss & Exit")

    c.print(Panel(menu_table, title="[bold green]Action Menu[/bold green]", border_style="green"))
    return normalize_menu_choice(prompt_func("Select an option [0-4]: "))


def execute_menu_action(
    choice: str,
    state: BootState | None = None,
    console: Console | None = None,
) -> bool:
    """Execute the selected menu action.

    Returns True if the menu loop should continue, False if it should exit.
    """
    c = console or Console()
    normalized_choice = normalize_menu_choice(choice)

    if normalized_choice == "0":
        c.print("[dim]Exiting DeskPilot. Have a productive day![/dim]")
        return False
    elif normalized_choice == "1":
        c.print("[cyan]Starting Screenshot Triage Agent...[/cyan]")
        c.print("[yellow]Screenshot Triage Agent (LangGraph & Notion) will be configured in Task 7.[/yellow]")
        return True
    elif normalized_choice == "2":
        c.print("[cyan]Starting Downloads Folder Cleanup Agent...[/cyan]")
        c.print("[yellow]Downloads Cleanup Agent will be configured in Task 8.[/yellow]")
        return True
    elif normalized_choice == "3":
        c.print("[cyan]Cleaning Floorp Bookmarks...[/cyan]")
        c.print("[yellow]Floorp bookmark cleanup completed (stub).[/yellow]")
        return True
    elif normalized_choice == "4":
        c.print("[cyan]Executing interactive winget upgrade...[/cyan]")
        try:
            subprocess.run(["winget", "upgrade", "--all", "--include-unknown"], check=False)
        except FileNotFoundError:
            c.print("[red]winget executable not found on this system.[/red]")
        except Exception as e:
            c.print(f"[red]Failed to run winget upgrade: {e}[/red]")
        return True
    else:
        c.print(f"[bold red]Invalid option '{choice}'. Please select an option between 0 and 4.[/bold red]")
        return True


async def main_async(
    console: Console | None = None,
    settings: Settings | None = None,
    prompt_func: Callable[[str], str] = input,
) -> int:
    """Asynchronous entry point for the DeskPilot morning boot orchestrator."""
    c = console or Console()
    if settings is None:
        try:
            settings = load_settings()
        except Exception as e:
            c.print(f"[red]Error loading configuration: {e}[/red]")
            return 1

    # Run Phase 1 boot sequence concurrently
    state = await run_phase1_boot_sequence(settings)

    # Render terminal dashboard
    render_dashboard(state, console=c)

    # Interactive menu loop
    while True:
        try:
            choice = prompt_action_menu(console=c, prompt_func=prompt_func)
        except (KeyboardInterrupt, EOFError):
            c.print("\n[dim]Dismissed. Exiting DeskPilot.[/dim]")
            return 0

        should_continue = execute_menu_action(choice, state=state, console=c)
        if not should_continue:
            return 0


def main() -> None:
    """DeskPilot CLI entry point."""
    try:
        sys.exit(asyncio.run(main_async()))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
