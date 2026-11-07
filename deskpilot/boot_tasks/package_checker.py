"""Asynchronous winget package update auditor for boot sequence."""

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


def parse_winget_output(
    output: str = "",
    ignore_packages: list[str] | None = None,
    raw_output: str | None = None,
) -> list[WingetUpdateItem]:
    """Parse tabular winget upgrade output into a list of WingetUpdateItem.

    Safely handles column alignments, variable column widths, optional Source column,
    trailing summary footers, and filters out packages matching ignore_packages.
    """
    text = raw_output if raw_output is not None else output
    if not text or not text.strip():
        return []

    # Strip ANSI terminal escape codes
    clean = re.sub(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", text)
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

        if ignore_packages:
            name_lower = name.lower()
            id_lower = id_val.lower()
            name_nospace = name_lower.replace(" ", "")
            id_nospace = id_lower.replace(" ", "")
            should_ignore = False
            for pattern in ignore_packages:
                p = pattern.strip().lower()
                if not p:
                    continue
                p_nospace = p.replace(" ", "")
                if (
                    p in name_lower
                    or p in id_lower
                    or (p_nospace and (p_nospace in name_nospace or p_nospace in id_nospace))
                ):
                    should_ignore = True
                    break
            if should_ignore:
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

    ignore_packages = config.ignore_packages if config is not None else None
    updates = parse_winget_output(stdout, ignore_packages=ignore_packages)
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

class WingetPackageDetail(BaseModel):
    """Detailed changelog and metadata for a specific winget package."""

    package_id: str
    release_notes: str = ""
    release_notes_url: str = ""
    description: str = ""
    publisher: str = ""
    homepage: str = ""
    error: str | None = None


def parse_winget_show_output(text: str, package_id: str = "") -> WingetPackageDetail:
    """Parse the stdout of winget show into WingetPackageDetail."""
    if not text or not text.strip():
        return WingetPackageDetail(package_id=package_id)

    clean = re.sub(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", text)
    lines = [line.strip("\r") for line in clean.split("\n")]

    release_notes_url = ""
    release_notes_lines: list[str] = []
    in_release_notes = False
    description_lines: list[str] = []
    in_description = False
    publisher = ""
    homepage = ""

    known_headers = (
        "Release Notes Url:",
        "Release Notes:",
        "Documentation:",
        "Installer:",
        "Version:",
        "Publisher:",
        "Publisher Url:",
        "Publisher Support Url:",
        "Author:",
        "Moniker:",
        "Description:",
        "Homepage:",
        "License:",
        "License Url:",
        "Privacy Url:",
        "Copyright:",
        "Copyright Url:",
        "Tags:",
        "Agreements:",
    )

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Release Notes Url:"):
            release_notes_url = stripped.split(":", 1)[1].strip()
            in_release_notes = False
            in_description = False
            continue

        if stripped.startswith("Homepage:"):
            homepage = stripped.split(":", 1)[1].strip()
            in_release_notes = False
            in_description = False
            continue

        if stripped.startswith("Publisher:"):
            publisher = stripped.split(":", 1)[1].strip()
            in_release_notes = False
            in_description = False
            continue

        if stripped.startswith("Release Notes:"):
            in_release_notes = True
            in_description = False
            rest = stripped.split(":", 1)[1].strip()
            if rest:
                release_notes_lines.append(rest)
            continue

        if stripped.startswith("Description:"):
            in_description = True
            in_release_notes = False
            rest = stripped.split(":", 1)[1].strip()
            if rest:
                description_lines.append(rest)
            continue

        if in_release_notes:
            if not line.startswith(" ") and not line.startswith("\t") and any(stripped.startswith(h) for h in known_headers):
                in_release_notes = False
            else:
                release_notes_lines.append(line)

        elif in_description:
            if not line.startswith(" ") and not line.startswith("\t") and any(stripped.startswith(h) for h in known_headers):
                in_description = False
            else:
                description_lines.append(line)

    return WingetPackageDetail(
        package_id=package_id,
        release_notes="\n".join(release_notes_lines).strip(),
        release_notes_url=release_notes_url,
        description="\n".join(description_lines).strip(),
        publisher=publisher,
        homepage=homepage,
    )


def _run_winget_show_sync(package_id: str, timeout_secs: int = 10) -> tuple[str, str, int]:
    """Execute winget show package synchronously with a timeout."""
    proc = subprocess.run(
        ["winget", "show", "--id", package_id, "--exact", "--accept-source-agreements"],
        capture_output=True,
        text=True,
        timeout=timeout_secs,
        encoding="utf-8",
        errors="replace",
    )
    return proc.stdout, proc.stderr, proc.returncode


async def get_winget_package_detail(package_id: str, timeout_secs: int = 10) -> WingetPackageDetail:
    """Asynchronously fetch package details and release notes for a package."""
    try:
        stdout, stderr, returncode = await asyncio.to_thread(
            _run_winget_show_sync,
            package_id,
            timeout_secs,
        )
    except (subprocess.TimeoutExpired, asyncio.TimeoutError):
        return WingetPackageDetail(package_id=package_id, error=f"Timeout fetching changelog for {package_id}")
    except FileNotFoundError:
        return WingetPackageDetail(package_id=package_id, error="winget not found")
    except Exception as e:
        return WingetPackageDetail(package_id=package_id, error=str(e))

    detail = parse_winget_show_output(stdout, package_id=package_id)
    if returncode != 0 and not detail.release_notes and not detail.release_notes_url:
        detail.error = stderr.strip() or f"winget show failed with code {returncode}"
    return detail

