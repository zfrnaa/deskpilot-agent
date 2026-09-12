"""LangGraph workflow definition and nodes for DeskPilot Notion Read-Later Agent."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from deskpilot.agent_tasks.read_later_agent.notion_reader import (
    query_unread_items,
    update_item_status,
)
from deskpilot.agent_tasks.read_later_agent.state import (
    ReadLaterAgentState,
    ReadLaterItem,
    create_initial_state,
)
from deskpilot.config import Settings

DEFAULT_PRIORITY_KEYWORDS = {
    "priority",
    "important",
    "urgent",
    "must read",
    "p0",
    "p1",
    "featured",
    "top",
}


async def fetch_unread_items_node(
    state: ReadLaterAgentState,
    client: Any = None,
) -> dict[str, Any]:
    """Node 1: Queries Notion database for items where status equals 'To Be Read'."""
    db_id = state.get("database_id", "")
    errors = list(state.get("errors", []))
    cl = state.get("client") or client

    if not db_id:
        return {
            "unread_items": [],
            "errors": errors + ["Database ID is not configured or empty"],
        }

    if cl is None:
        return {
            "unread_items": [],
            "errors": errors + ["Notion client is not configured or disabled"],
        }

    try:
        items = await query_unread_items(database_id=db_id, client=cl)
        return {"unread_items": items}
    except Exception as e:
        return {
            "unread_items": [],
            "errors": errors + [f"Failed to fetch unread items from Notion: {e}"],
        }


def _is_priority_item(item: ReadLaterItem, keywords: set[str]) -> bool:
    """Check if any of the item's tags match known priority keywords."""
    for tag in item.tags:
        cleaned = tag.strip().lower()
        if cleaned in keywords or any(kw in cleaned for kw in keywords):
            return True
    return False


def select_daily_recommendation(
    state: ReadLaterAgentState,
    priority_tags: set[str] | None = None,
) -> dict[str, Any]:
    """Node 2: Selects 1 top pick (priority or oldest) for the user's morning review."""
    items = state.get("unread_items", [])
    if not items:
        return {"recommended_item": None}

    keywords = priority_tags if priority_tags is not None else DEFAULT_PRIORITY_KEYWORDS

    def sort_key(item: ReadLaterItem) -> tuple[int, str]:
        # Priority items come first (priority=0, non-priority=1)
        priority_order = 0 if _is_priority_item(item, keywords) else 1
        # Then sort by added_date ascending (oldest first); None dates go to the end
        date_str = item.added_date if item.added_date else "9999-99-99"
        return (priority_order, date_str)

    best_item = min(items, key=sort_key)
    return {"recommended_item": best_item}


def interactive_or_action_node(
    state: ReadLaterAgentState,
    action_func: Callable[[ReadLaterItem | None], Any] | None = None,
) -> dict[str, Any]:
    """Node 3: Allows marking recommendation as read interactively or programmatically."""
    rec = state.get("recommended_item")
    if rec is None:
        return {"marked_as_read_id": None}

    auto_mark = state.get("auto_mark_read", False)
    interactive = state.get("interactive", False)

    # 1. Programmatic action callback
    if action_func is not None:
        action = action_func(rec)
        if action in (True, "r", "R", "read", "READ"):
            return {"marked_as_read_id": rec.page_id}
        return {"marked_as_read_id": None}

    # 2. Automated mark as read flag
    if auto_mark:
        return {"marked_as_read_id": rec.page_id}

    # 3. Interactive console review
    if interactive:
        try:
            from rich.console import Console
            from rich.panel import Panel

            console = Console()
            lines = [
                f"[bold cyan]Title:[/bold cyan] {rec.title}",
                f"[bold]URL:[/bold] {rec.url or 'N/A'}",
                f"[bold]Added:[/bold] {rec.added_date or 'N/A'}",
                f"[bold]Tags:[/bold] {', '.join(rec.tags) if rec.tags else 'None'}",
            ]
            console.print(
                Panel(
                    "\n".join(lines),
                    title="[bold green]DeskPilot Read Later - Daily Pick[/bold green]",
                    border_style="green",
                )
            )

            if sys.stdin.isatty():
                choice = input("\nMark as read? [r/N]: ").strip().lower()
                if choice in ("r", "read"):
                    return {"marked_as_read_id": rec.page_id}
        except Exception:
            pass

    return {"marked_as_read_id": None}


async def update_notion_status_node(
    state: ReadLaterAgentState,
    client: Any = None,
) -> dict[str, Any]:
    """Node 4: Patches the Notion page property Status -> 'read' upon request."""
    marked_id = state.get("marked_as_read_id")
    if not marked_id:
        return {"status_updated": False}

    cl = state.get("client") or client
    errors = list(state.get("errors", []))

    success = await update_item_status(page_id=marked_id, new_status="read", client=cl)
    if success:
        rec = state.get("recommended_item")
        if rec and rec.page_id == marked_id:
            rec.status = "read"
        return {"status_updated": True, "recommended_item": rec}
    else:
        errors.append(f"Failed to update Notion status for page {marked_id}")
        return {"status_updated": False, "errors": errors}


def build_read_later_graph(
    client: Any = None,
    database_id: str = "",
    auto_mark_read: bool = False,
    interactive: bool = False,
    action_func: Callable[[ReadLaterItem | None], Any] | None = None,
) -> CompiledStateGraph:
    """Build and compile the LangGraph workflow for Notion Read Later Agent."""
    builder = StateGraph(ReadLaterAgentState)

    async def _fetch(state: ReadLaterAgentState) -> dict[str, Any]:
        cl = state.get("client") or client
        return await fetch_unread_items_node(state, client=cl)

    def _recommend(state: ReadLaterAgentState) -> dict[str, Any]:
        return select_daily_recommendation(state)

    def _review(state: ReadLaterAgentState) -> dict[str, Any]:
        state_copy = dict(state)
        if "auto_mark_read" not in state_copy:
            state_copy["auto_mark_read"] = auto_mark_read
        if "interactive" not in state_copy:
            state_copy["interactive"] = interactive
        fn = state_copy.get("action_func") or action_func
        return interactive_or_action_node(state_copy, action_func=fn)

    async def _update(state: ReadLaterAgentState) -> dict[str, Any]:
        cl = state.get("client") or client
        return await update_notion_status_node(state, client=cl)

    builder.add_node("fetch_unread_items", _fetch)
    builder.add_node("select_daily_recommendation", _recommend)
    builder.add_node("interactive_or_action_node", _review)
    builder.add_node("update_notion_status", _update)

    builder.set_entry_point("fetch_unread_items")
    builder.add_edge("fetch_unread_items", "select_daily_recommendation")
    builder.add_edge("select_daily_recommendation", "interactive_or_action_node")
    builder.add_edge("interactive_or_action_node", "update_notion_status")
    builder.add_edge("update_notion_status", END)

    return builder.compile()


async def run_read_later_flow(
    settings: Settings,
    client: Any = None,
    auto_mark_read: bool = False,
    interactive: bool = False,
    action_func: Callable[[ReadLaterItem | None], Any] | None = None,
) -> ReadLaterAgentState:
    """High-level entrypoint to execute Notion read-later flow using configured settings."""
    db_id = settings.notion.read_later_database_id
    token = settings.notion.token
    enabled = settings.notion.enabled

    if not enabled or not token or not db_id:
        initial_state: ReadLaterAgentState = {
            "database_id": db_id,
            "unread_items": [],
            "recommended_item": None,
            "marked_as_read_id": None,
            "status_updated": False,
            "errors": [
                "Notion read-later integration is not configured or disabled (missing token or database_id)"
            ],
            "auto_mark_read": auto_mark_read,
            "interactive": interactive,
        }
        return initial_state

    if client is None:
        from notion_client import Client

        client = Client(auth=token)

    graph = build_read_later_graph(
        client=client,
        database_id=db_id,
        auto_mark_read=auto_mark_read,
        interactive=interactive,
        action_func=action_func,
    )

    initial_state = {
        "database_id": db_id,
        "unread_items": [],
        "recommended_item": None,
        "marked_as_read_id": None,
        "status_updated": False,
        "errors": [],
        "auto_mark_read": auto_mark_read,
        "interactive": interactive,
    }

    return await graph.ainvoke(initial_state)
