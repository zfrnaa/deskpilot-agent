"""Asynchronous winget package update auditor for morning boot sequence."""

from __future__ import annotations

import asyncio
import re
import subprocess
from typing import Any

from pydantic import BaseModel, Field

from deskpilot.config import WingetConfig


class WingetUpdateItem(BaseModel):
    """Information regarding an available package update."""

    name: str
    id: str
    version: str
    available_version: str
    source: str = ""


class WingetUpdateResult(BaseModel):
    """Aggregated output from winget upgrade query."""

    total_count: int = 0
    updates: list[WingetUpdateItem] = Field(default_factory=list)
    timed_out: bool = False
    error: str | None = None


def parse_winget_output(output: str) -> list[WingetUpdateItem]:
    """Parse tabular winget upgrade output into a list of WingetUpdateItem.

    Safely handles column alignments, variable column widths, optional Source column,
    and trailing summary footers.
    """
    if not output or not output.strip():
        return []

    # Strip ANSI terminal escape codes
    clean = re.sub(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", output)
    lines = [line.strip("\r") for line in clean.split("\n")]

    # Find the separator line consisting of dashes directly below the header
    header_idx = -1
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped and re.fullmatch(r"[- ]+", stripped) and stripped.count("-") >= 10:
            if idx > 0:
                prev_line = lines[idx - 1]
                if (
                    "Name" in prev_line
                    and "Id" in prev_line
                    and "Version" in prev_line
                    and "Available" in prev_line
                ):
                    header_idx = idx - 1
                    break

    if header_idx == -1:
        return []

    header_line = lines[header_idx]

    m_name = re.search(r"\bName\b", header_line)
    m_id = re.search(r"\bId\b", header_line)
    m_version = re.search(r"\bVersion\b", header_line)
    m_available = re.search(r"\bAvailable\b", header_line)
    m_source = re.search(r"\bSource\b", header_line)

    if not (m_name and m_id and m_version and m_available):
        return []

    name_start = m_name.start()
    id_start = m_id.start()
    version_start = m_version.start()
    available_start = m_available.start()
    source_start = m_source.start() if m_source else None

    # Verify expected column ordering
    if not (name_start < id_start < version_start < available_start):
        return []
    if source_start is not None and source_start <= available_start:
        source_start = None

    updates: list[WingetUpdateItem] = []

    # Parse rows following the separator line
    for line in lines[header_idx + 2 :]:
        trimmed = line.strip()
        if not trimmed:
            continue

        # Check for summary footer indicators
        if re.search(r"\b\d+\s+upgrades?\s+available\b", trimmed, re.IGNORECASE):
            break
        if re.search(
            r"\b(?:\d+\s+package\(s\)|package(?:\(s\)|s)?\s+have|have\s+pins\b)",
            trimmed,
            re.IGNORECASE,
        ):
            break
        if trimmed.startswith("-"):
            continue

        # Check if line has enough width for name and id
        if len(line) < id_start:
            continue

        name = line[name_start:id_start].strip()
        id_val = (
            line[id_start:version_start].strip()
            if len(line) >= version_start
            else line[id_start:].strip()
        )

        if not name or not id_val:
            continue

        if len(line) < available_start:
            continue

        version = line[version_start:available_start].strip()

        if source_start is not None and len(line) >= source_start:
            available_version = line[available_start:source_start].strip()
            source = line[source_start:].strip()
        else:
            available_version = line[available_start:].strip()
            source = ""

        if not available_version:
            continue

        updates.append(
            WingetUpdateItem(
                name=name,
                id=id_val,
                version=version,
                available_version=available_version,
                source=source,
            )
        )

    return updates


def _run_winget_sync(timeout_secs: int) -> tuple[str, str, int]:
    """Execute winget upgrade check synchronously with a timeout."""
    proc = subprocess.run(
        ["winget", "upgrade", "--include-unknown"],
        capture_output=True,
        text=True,
        timeout=timeout_secs,
        encoding="utf-8",
        errors="replace",
    )
    return proc.stdout, proc.stderr, proc.returncode


async def check_winget_updates(
    timeout_secs: int = 15,
    config: WingetConfig | None = None,
) -> WingetUpdateResult:
    """Asynchronously query winget for available package updates without installing.

    Args:
        timeout_secs: Maximum execution duration in seconds.
        config: Optional WingetConfig instance.

    Returns:
        WingetUpdateResult with parsed packages and count.
    """
    effective_timeout = timeout_secs
    if config is not None:
        if not config.enabled:
            return WingetUpdateResult()
        if timeout_secs == 15 and config.timeout_secs != 15:
            effective_timeout = config.timeout_secs

    try:
        stdout, stderr, returncode = await asyncio.to_thread(
            _run_winget_sync,
            effective_timeout,
        )
    except (subprocess.TimeoutExpired, asyncio.TimeoutError):
        return WingetUpdateResult(
            timed_out=True,
            error=f"winget upgrade timed out after {effective_timeout}s",
        )
    except FileNotFoundError:
        return WingetUpdateResult(
            error="winget executable not found",
        )
    except OSError as e:
        return WingetUpdateResult(
            error=f"winget execution failed: {e}",
        )

    updates = parse_winget_output(stdout)
    if updates:
        return WingetUpdateResult(
            total_count=len(updates),
            updates=updates,
            timed_out=False,
            error=None,
        )

    # Check for normal benign messages indicating no updates
    combined = f"{stdout}\n{stderr}".lower()
    benign_phrases = [
        "no installed package found matching input criteria",
        "no applicable update found",
        "no newer package versions found",
        "0 upgrades available",
    ]
    if any(phrase in combined for phrase in benign_phrases) or returncode == 0:
        return WingetUpdateResult(
            total_count=0,
            updates=[],
            timed_out=False,
            error=None,
        )

    error_msg = stderr.strip() or stdout.strip() or f"winget exited with code {returncode}"
    return WingetUpdateResult(
        total_count=0,
        updates=[],
        timed_out=False,
        error=error_msg,
    )
