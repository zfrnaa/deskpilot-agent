"""LangGraph workflow definition and nodes for Downloads Hygiene Agent."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from deskpilot.agent_tasks.downloads_agent.actions import (
    assemble_action_plan,
    categorize_items,
    execute_approved_actions,
    format_bytes,
    format_plan_summary,
    scan_directory,
)
from deskpilot.agent_tasks.downloads_agent.state import (
    DownloadItem,
    DownloadsAgentState,
    create_initial_state,
)
from deskpilot.config import Settings


def scan_downloads(state: DownloadsAgentState) -> dict[str, Any]:
    """Node 1: Scan target downloads directory and catalog files with sizes and ages."""
    d_dir = state.get("downloads_dir")
    errors = list(state.get("errors", []))

    if not d_dir:
        return {"items": [], "errors": errors + ["downloads_dir is not set"]}

    items, scan_errors = scan_directory(d_dir)
    return {
        "items": items,
        "errors": errors + scan_errors,
    }


def categorize_and_analyze(state: DownloadsAgentState) -> dict[str, Any]:
    """Node 2: Identify duplicates, stale installers, archives, documents, and propose actions."""
    items = state.get("items", [])
    max_age = state.get("installer_max_age_days", 30)
    updated_items = categorize_items(items, installer_max_age_days=max_age)
    return {"items": updated_items}


def propose_plan(state: DownloadsAgentState) -> dict[str, Any]:
    """Node 3: Assemble categorized action plan from actionable items."""
    items = state.get("items", [])
    action_plan = assemble_action_plan(items)
    return {"action_plan": action_plan}


def human_review_node(
    state: DownloadsAgentState,
    review_func: Callable[[dict[str, list[DownloadItem]]], list[str]] | None = None,
) -> dict[str, Any]:
    """Node 4: Present proposed actions in a Rich table and gate execution behind approval."""
    action_plan = state.get("action_plan", {})
    auto_approve = state.get("auto_approve", False)

    # 1. Custom programmatic review handler (used in tests/automation)
    if review_func is not None:
        approved = review_func(action_plan)
        return {"approved_actions": approved}

    # 2. Automated approval mode
    if auto_approve:
        return {"approved_actions": list(action_plan.keys())}

    # 3. Interactive console review
    if not action_plan:
        return {"approved_actions": []}

    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(title="[bold cyan]Downloads Hygiene Proposed Actions[/bold cyan]")
        table.add_column("Category Key", style="cyan", width=22)
        table.add_column("Action", style="bold magenta", width=12)
        table.add_column("Files", justify="right", style="green", width=8)
        table.add_column("Total Size", justify="right", style="yellow", width=12)
        table.add_column("Sample Files", style="dim")

        for key, group in action_plan.items():
            action_type = group[0].proposed_action.value.upper() if group else "KEEP"
            count = len(group)
            total_size = sum(item.size_bytes for item in group)
            samples = ", ".join(item.filename for item in group[:3])
            if count > 3:
                samples += f", ... (+{count - 3} more)"
            table.add_row(key, action_type, str(count), format_bytes(total_size), samples)

        console.print(table)

        summaries = format_plan_summary(action_plan)
        for s in summaries:
            console.print(f" [bold green]•[/bold green] {s}")

        if sys.stdin.isatty():
            choice = input("\nApprove executing proposed downloads cleanup actions? [y/N]: ").strip().lower()
            if choice in {"y", "yes"}:
                return {"approved_actions": list(action_plan.keys())}
    except Exception:
        pass

    return {"approved_actions": []}


def execute_actions(state: DownloadsAgentState) -> dict[str, Any]:
    """Node 5: Safely execute approved file deletions and archive moves."""
    items = state.get("items", [])
    approved = state.get("approved_actions", [])
    archive_dir = state.get("archive_dir", Path("Archive"))
    current_errors = list(state.get("errors", []))

    freed, moved, action_errors = execute_approved_actions(
        items=items,
        approved_actions=approved,
        archive_dir=archive_dir,
    )

    return {
        "items": items,
        "total_bytes_freed": freed,
        "total_files_moved": moved,
        "errors": current_errors + action_errors,
    }


def build_downloads_hygiene_graph(
    review_func: Callable[[dict[str, list[DownloadItem]]], list[str]] | None = None,
    auto_approve: bool = False,
) -> CompiledStateGraph:
    """Build and compile the LangGraph workflow for Downloads Hygiene."""
    builder = StateGraph(DownloadsAgentState)

    def _scan(state: DownloadsAgentState) -> dict[str, Any]:
        return scan_downloads(state)

    def _categorize(state: DownloadsAgentState) -> dict[str, Any]:
        return categorize_and_analyze(state)

    def _propose(state: DownloadsAgentState) -> dict[str, Any]:
        return propose_plan(state)

    def _review(state: DownloadsAgentState) -> dict[str, Any]:
        state_copy = dict(state)
        if "auto_approve" not in state_copy:
            state_copy["auto_approve"] = auto_approve
        return human_review_node(state_copy, review_func=review_func)

    def _execute(state: DownloadsAgentState) -> dict[str, Any]:
        return execute_actions(state)

    builder.add_node("scan_downloads", _scan)
    builder.add_node("categorize_and_analyze", _categorize)
    builder.add_node("propose_plan", _propose)
    builder.add_node("human_review_node", _review)
    builder.add_node("execute_actions", _execute)

    builder.set_entry_point("scan_downloads")
    builder.add_edge("scan_downloads", "categorize_and_analyze")
    builder.add_edge("categorize_and_analyze", "propose_plan")
    builder.add_edge("propose_plan", "human_review_node")
    builder.add_edge("human_review_node", "execute_actions")
    builder.add_edge("execute_actions", END)

    return builder.compile(name="DownloadsHygieneAgent")


async def run_downloads_hygiene(
    settings: Settings,
    auto_approve: bool = False,
    review_func: Callable[[dict[str, list[DownloadItem]]], list[str]] | None = None,
) -> DownloadsAgentState:
    """High-level entrypoint to execute downloads hygiene agent using configured settings."""
    downloads_dir = settings.downloads.get_resolved_directory()
    installer_max_age_days = settings.downloads.stale_days
    archive_dir = downloads_dir / "Archive"

    graph = build_downloads_hygiene_graph(
        review_func=review_func,
        auto_approve=auto_approve,
    )

    initial_state = create_initial_state(
        downloads_dir=downloads_dir,
        archive_dir=archive_dir,
        installer_max_age_days=installer_max_age_days,
        auto_approve=auto_approve,
    )

    return await graph.ainvoke(
        initial_state,
        config={
            "run_name": "DownloadsHygieneAgent",
            "tags": ["agent:downloads_hygiene"],
            "metadata": {
                "auto_approve": auto_approve,
                "stale_days": settings.downloads.stale_days,
            },
        },
    )

