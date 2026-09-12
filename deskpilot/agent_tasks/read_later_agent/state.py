"""State models and schema for DeskPilot Notion Read-Later Agent."""

from __future__ import annotations

from typing import Any
from typing_extensions import TypedDict

from pydantic import BaseModel, Field


class ReadLaterItem(BaseModel):
    """Represents an article or bookmark item stored in the Notion Read Later database."""

    page_id: str
    title: str
    url: str | None = None
    status: str = "To Be Read"
    added_date: str | None = None
    tags: list[str] = Field(default_factory=list)


class ReadLaterAgentState(TypedDict, total=False):
    """LangGraph state representation for the Read Later Agent workflow."""

    database_id: str
    unread_items: list[ReadLaterItem]
    recommended_item: ReadLaterItem | None
    marked_as_read_id: str | None
    status_updated: bool
    errors: list[str]
    auto_mark_read: bool
    interactive: bool
    action_func: Any
    client: Any


def create_initial_state(
    database_id: str = "",
    auto_mark_read: bool = False,
    interactive: bool = False,
) -> ReadLaterAgentState:
    """Initialize a default state dictionary for the Read Later workflow."""
    return {
        "database_id": database_id,
        "unread_items": [],
        "recommended_item": None,
        "marked_as_read_id": None,
        "status_updated": False,
        "errors": [],
        "auto_mark_read": auto_mark_read,
        "interactive": interactive,
    }
