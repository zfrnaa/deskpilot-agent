"""Windows power plan management via powercfg.exe."""

from __future__ import annotations

import re
import subprocess
import sys

_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_POWERCFG_LINE_RE = re.compile(
    r"Power Scheme GUID:\s*([0-9a-fA-F-]{36})\s+\(([^)]+)\)\s*(\*?)"
)


def _run_powercfg(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run powercfg.exe with hidden console window on Windows."""
    kwargs: dict = {"capture_output": True, "text": True, "check": False}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return subprocess.run(["powercfg", *args], **kwargs)


def list_power_plans() -> list[tuple[str, str, bool]]:
    """Return list of (guid, name, is_active) from `powercfg /list`."""
    proc = _run_powercfg(["/list"])
    if proc.returncode != 0:
        return []
    plans: list[tuple[str, str, bool]] = []
    for line in proc.stdout.splitlines():
        match = _POWERCFG_LINE_RE.search(line)
        if match:
            guid, name, star = match.groups()
            plans.append((guid.lower(), name.strip(), star == "*"))
    return plans


def resolve_power_plan_guid(plan_or_guid: str) -> str | None:
    """Resolve a Windows power plan GUID or display name to its GUID."""
    candidate = plan_or_guid.strip()
    if not candidate:
        return None
    if _GUID_RE.match(candidate):
        return candidate.lower()

    target = candidate.lower()
    for guid, name, _ in list_power_plans():
        if name.lower() == target:
            return guid
    return None


def get_active_power_plan_guid() -> str | None:
    """Return the currently active Windows power plan GUID, or None."""
    for guid, _, is_active in list_power_plans():
        if is_active:
            return guid
    return None


def set_power_plan(plan_or_guid: str) -> tuple[bool, str | None]:
    """Activate a Windows power plan by GUID or display name."""
    guid = resolve_power_plan_guid(plan_or_guid)
    if not guid:
        return False, f"Power plan '{plan_or_guid}' not found."

    proc = _run_powercfg(["/setactive", guid])
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "powercfg /setactive failed").strip()
        return False, err
    return True, None
