"""Downloads Hygiene LangGraph Agent for DeskPilot."""

from __future__ import annotations

from deskpilot.agent_tasks.downloads_agent.actions import (
    assemble_action_plan,
    categorize_item,
    categorize_items,
    execute_approved_actions,
    format_bytes,
    format_plan_summary,
    scan_directory,
)
from deskpilot.agent_tasks.downloads_agent.graph import (
    build_downloads_hygiene_graph,
    categorize_and_analyze,
    execute_actions,
    get_hygiene_state,
    human_review_node,
    propose_plan,
    resume_downloads_hygiene,
    run_downloads_hygiene,
    scan_downloads,
)
from deskpilot.agent_tasks.downloads_agent.state import (
    DownloadFileCategory,
    DownloadItem,
    DownloadsAgentState,
    ProposedAction,
    create_initial_state,
)

__all__ = [
    "DownloadFileCategory",
    "ProposedAction",
    "DownloadItem",
    "DownloadsAgentState",
    "create_initial_state",
    "scan_directory",
    "categorize_item",
    "categorize_items",
    "assemble_action_plan",
    "format_plan_summary",
    "execute_approved_actions",
    "scan_downloads",
    "categorize_and_analyze",
    "propose_plan",
    "human_review_node",
    "execute_actions",
    "build_downloads_hygiene_graph",
    "run_downloads_hygiene",
    "get_hygiene_state",
    "resume_downloads_hygiene",
]
