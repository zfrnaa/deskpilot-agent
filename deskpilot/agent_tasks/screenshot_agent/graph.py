"""LangGraph workflow definition and nodes for Screenshot Triage Agent."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from deskpilot.agent_tasks.screenshot_agent.notion_sync import (
    cleanup_synced_files,
    sync_approved_items,
)
from deskpilot.agent_tasks.screenshot_agent.state import (
    ScreenshotAgentState,
    ScreenshotItem,
    create_initial_state,
)
from deskpilot.agent_tasks.screenshot_agent.vision import (
    get_default_vision_llm,
    triage_screenshots,
)
from deskpilot.config import Settings


def scan_screenshots(state: ScreenshotAgentState) -> dict[str, Any]:
    """Scan target directory for unprocessed screenshot image files."""
    s_dir = state.get("screenshots_dir")
    errors = list(state.get("errors", []))

    if not s_dir:
        return {"items": [], "errors": errors + ["screenshots_dir is not set"]}

    path = Path(s_dir).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        return {
            "items": [],
            "errors": errors + [f"Screenshots directory does not exist: {path}"],
        }

    valid_extensions = {".png", ".jpg", ".jpeg"}
    image_files: list[Path] = []
    try:
        for f in path.iterdir():
            if f.is_file() and f.suffix.lower() in valid_extensions:
                image_files.append(f)
    except Exception as e:
        return {"items": [], "errors": errors + [f"Error scanning directory {path}: {e}"]}

    # Deterministic sort by name (or mtime if available)
    image_files.sort(key=lambda p: p.name.lower())

    max_imgs = state.get("max_images", 50)
    selected_files = image_files[:max_imgs] if max_imgs > 0 else image_files

    items = [ScreenshotItem(path=f, filename=f.name) for f in selected_files]
    return {"items": items, "errors": errors}


def vision_triage(
    state: ScreenshotAgentState,
    llm: Any = None,
) -> dict[str, Any]:
    """Run multimodal classification on screenshot items."""
    items = state.get("items", [])
    updated_items, triage_errors = triage_screenshots(items, llm=llm)
    current_errors = list(state.get("errors", [])) + triage_errors
    return {"items": updated_items, "errors": current_errors}


def cluster_items(state: ScreenshotAgentState) -> dict[str, Any]:
    """Group items by their assigned cluster_tag."""
    items = state.get("items", [])
    clusters: dict[str, list[ScreenshotItem]] = {}
    for item in items:
        tag = item.cluster_tag or "General"
        clusters.setdefault(tag, []).append(item)
    return {"clusters": clusters}


def human_review_node(
    state: ScreenshotAgentState,
    review_func: Callable[[dict[str, list[ScreenshotItem]]], list[str]] | None = None,
) -> dict[str, Any]:
    """Review clustered items and determine which cluster tags are approved for sync."""
    clusters = state.get("clusters", {})
    auto_approve = state.get("auto_approve", False)

    if review_func is not None:
        approved = review_func(clusters)
        return {"approved_cluster_keys": approved}

    if auto_approve:
        # In automated mode, approve any cluster containing at least one NOTION_NOTE item
        approved = [
            tag
            for tag, group in clusters.items()
            if any(item.classification == "NOTION_NOTE" for item in group)
        ]
        return {"approved_cluster_keys": approved}

    # Interactive console review
    approved_clusters: list[str] = []
    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(title="[bold cyan]Screenshot Triage Clusters[/bold cyan]")
        table.add_column("Cluster Tag", style="cyan", width=20)
        table.add_column("Total Items", justify="right", style="green", width=12)
        table.add_column("Notion Notes", justify="right", style="yellow", width=12)
        table.add_column("Keep Local", justify="right", style="dim", width=12)

        has_notes = False
        for tag, group in clusters.items():
            notes = sum(1 for i in group if i.classification == "NOTION_NOTE")
            keeps = len(group) - notes
            if notes > 0:
                has_notes = True
            table.add_row(tag, str(len(group)), str(notes), str(keeps))

        console.print(table)

        if not has_notes:
            console.print("[dim]No items classified as NOTION_NOTE. Nothing to sync.[/dim]")
            return {"approved_cluster_keys": []}

        if sys.stdin.isatty():
            choice = input("Approve syncing all NOTION_NOTE clusters to Notion? [y/N]: ").strip().lower()
            if choice in {"y", "yes"}:
                approved_clusters = [
                    tag
                    for tag, group in clusters.items()
                    if any(item.classification == "NOTION_NOTE" for item in group)
                ]
    except Exception:
        approved_clusters = []

    return {"approved_cluster_keys": approved_clusters}


def notion_sync(
    state: ScreenshotAgentState,
    notion_client: Any = None,
    parent_page_id: str = "",
    database_id: str = "",
) -> dict[str, Any]:
    """Synchronize approved NOTION_NOTE screenshots to Notion."""
    items = state.get("items", [])
    approved = state.get("approved_cluster_keys", [])
    synced_count, sync_errors = sync_approved_items(
        items=items,
        approved_cluster_keys=approved,
        notion_client=notion_client,
        parent_page_id=parent_page_id,
        database_id=database_id,
    )
    current_errors = list(state.get("errors", [])) + sync_errors
    return {
        "items": items,
        "synced_count": synced_count,
        "errors": current_errors,
    }


def cleanup_synced(
    state: ScreenshotAgentState,
    delete_synced_local: bool = True,
) -> dict[str, Any]:
    """Safely delete local screenshots only for confirmed synced items."""
    items = state.get("items", [])
    del_local = state.get("delete_synced_local", delete_synced_local)
    deleted_count, cleanup_errors = cleanup_synced_files(
        items=items,
        delete_synced_local=del_local,
    )
    current_errors = list(state.get("errors", [])) + cleanup_errors
    return {
        "items": items,
        "deleted_count": deleted_count,
        "errors": current_errors,
    }


def build_screenshot_triage_graph(
    llm: Any = None,
    notion_client: Any = None,
    parent_page_id: str = "",
    database_id: str = "",
    delete_synced_local: bool = True,
    auto_approve: bool = False,
    review_func: Callable[[dict[str, list[ScreenshotItem]]], list[str]] | None = None,
) -> CompiledStateGraph:
    """Build and compile the LangGraph workflow for screenshot triage."""
    builder = StateGraph(ScreenshotAgentState)

    def _scan(state: ScreenshotAgentState) -> dict[str, Any]:
        return scan_screenshots(state)

    def _triage(state: ScreenshotAgentState) -> dict[str, Any]:
        return vision_triage(state, llm=llm)

    def _cluster(state: ScreenshotAgentState) -> dict[str, Any]:
        return cluster_items(state)

    def _review(state: ScreenshotAgentState) -> dict[str, Any]:
        if "auto_approve" not in state:
            state["auto_approve"] = auto_approve
        return human_review_node(state, review_func=review_func)

    def _sync(state: ScreenshotAgentState) -> dict[str, Any]:
        p_id = state.get("parent_page_id", parent_page_id)
        db_id = state.get("database_id", database_id)
        return notion_sync(
            state,
            notion_client=notion_client,
            parent_page_id=p_id,
            database_id=db_id,
        )

    def _clean(state: ScreenshotAgentState) -> dict[str, Any]:
        del_flag = state.get("delete_synced_local", delete_synced_local)
        return cleanup_synced(state, delete_synced_local=del_flag)

    builder.add_node("scan_screenshots", _scan)
    builder.add_node("vision_triage", _triage)
    builder.add_node("cluster_items", _cluster)
    builder.add_node("human_review_node", _review)
    builder.add_node("notion_sync", _sync)
    builder.add_node("cleanup_synced", _clean)

    builder.set_entry_point("scan_screenshots")
    builder.add_edge("scan_screenshots", "vision_triage")
    builder.add_edge("vision_triage", "cluster_items")
    builder.add_edge("cluster_items", "human_review_node")
    builder.add_edge("human_review_node", "notion_sync")
    builder.add_edge("notion_sync", "cleanup_synced")
    builder.add_edge("cleanup_synced", END)

    return builder.compile()


async def run_screenshot_triage(
    settings: Settings,
    auto_approve: bool = False,
    llm: Any = None,
    notion_client: Any = None,
    review_func: Callable[[dict[str, list[ScreenshotItem]]], list[str]] | None = None,
) -> ScreenshotAgentState:
    """High-level entrypoint to execute screenshot triage agent using configured settings."""
    if llm is None and settings.gemini_api_key:
        llm = get_default_vision_llm(settings.gemini_api_key)

    if notion_client is None and settings.notion.enabled and settings.notion.token:
        from notion_client import Client

        notion_client = Client(auth=settings.notion.token)

    graph = build_screenshot_triage_graph(
        llm=llm,
        notion_client=notion_client,
        parent_page_id=settings.notion.parent_page_id,
        database_id="",
        delete_synced_local=settings.screenshots.delete_synced_local,
        auto_approve=auto_approve,
        review_func=review_func,
    )

    initial_state = create_initial_state(
        screenshots_dir=settings.screenshots.get_resolved_directory(),
        max_images=50,
        auto_approve=auto_approve,
        delete_synced_local=settings.screenshots.delete_synced_local,
    )

    return await graph.ainvoke(initial_state)
