"""State definitions and models for the Screenshot Triage Agent."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Literal, TypedDict

from pydantic import BaseModel, model_validator

from deskpilot.config import ScreenshotDestinationsConfig

ScreenshotClassification = Literal[
    "WORK_NOTES",
    "DUE_DILIGENCE",
    "BRAINSTORM",
    "LOCAL_KEEP",
    "NOTION_NOTE",
]


class ScreenshotItem(BaseModel):
    """Represents a single screenshot image during triage and synchronization."""

    path: Path
    filename: str = ""
    title: str = ""
    classification: ScreenshotClassification = "LOCAL_KEEP"
    cluster_tag: str = "General"
    rationale: str = ""
    is_synced: bool = False
    deleted_locally: bool = False

    @model_validator(mode="after")
    def populate_filename(self) -> ScreenshotItem:
        """Ensure filename is populated from path.name if empty."""
        if not self.filename and self.path:
            self.filename = self.path.name
        return self


class ScreenshotAgentState(TypedDict, total=False):
    """State schema for the Screenshot Triage LangGraph workflow."""

    screenshots_dir: Path
    max_images: int
    total_discovered: int
    items: list[ScreenshotItem]
    clusters: dict[str, list[ScreenshotItem]]
    approved_cluster_keys: list[str]
    synced_count: int
    deleted_count: int
    errors: list[str]
    auto_approve: bool
    delete_synced_local: bool
    destinations: ScreenshotDestinationsConfig
    parent_page_id: str
    database_id: str
    prompt_func: Any
    console_print: Any


def create_initial_state(
    screenshots_dir: Path,
    max_images: int = 10,
    auto_approve: bool = False,
    delete_synced_local: bool = True,
    destinations: ScreenshotDestinationsConfig | None = None,
    prompt_func: Any = None,
    console_print: Any = None,
) -> ScreenshotAgentState:
    """Create a fully initialized ScreenshotAgentState dictionary."""
    state: ScreenshotAgentState = {
        "screenshots_dir": screenshots_dir,
        "max_images": max_images,
        "items": [],
        "clusters": {},
        "approved_cluster_keys": [],
        "synced_count": 0,
        "deleted_count": 0,
        "errors": [],
        "auto_approve": auto_approve,
        "delete_synced_local": delete_synced_local,
    }
    if destinations is not None:
        state["destinations"] = destinations
    if prompt_func is not None:
        state["prompt_func"] = prompt_func
    if console_print is not None:
        state["console_print"] = console_print
    return state
