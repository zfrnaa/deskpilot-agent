"""Runtime state models for DeskPilot boot orchestrator."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BootState(BaseModel):
    """Execution state holding morning health checks, agenda, and agent findings."""

    model_config = ConfigDict(populate_by_name=True)

    timestamp: datetime = Field(default_factory=datetime.now)
    system_hygiene: Any | None = None
    floorp_bookmarks: Any | None = Field(default=None, alias="bookmarks")
    winget_updates: Any | None = None
    calendar_agenda: Any | None = None
    agent_findings: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)

    @property
    def bookmarks(self) -> Any | None:
        """Convenience alias for floorp_bookmarks."""
        return self.floorp_bookmarks

    @bookmarks.setter
    def bookmarks(self, value: Any | None) -> None:
        self.floorp_bookmarks = value

    def add_error(self, error: str) -> None:
        """Record an error encountered during task execution."""
        self.errors.append(error)

    def set_finding(self, agent_name: str, finding: Any) -> None:
        """Record or update findings from an agent or boot task."""
        self.agent_findings[agent_name] = finding

    def has_errors(self) -> bool:
        """Check if any errors were recorded during the boot sequence."""
        return len(self.errors) > 0