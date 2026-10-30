"""Background autonomous watcher daemon for DeskPilot."""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from deskpilot.config import Settings
from deskpilot.storage.store import SQLiteStore
from deskpilot.ui.notifications import send_windows_toast


DAEMON_NAMESPACE = ("daemon", "cadence")
DOWNLOADS_LAST_TRIGGER_KEY = "downloads_last_triage_ts"


def count_eligible_screenshots(screenshots_dir: Path) -> list[Path]:
    """Return list of unorganized image files in the screenshots directory."""
    if not screenshots_dir.exists() or not screenshots_dir.is_dir():
        return []
    valid_exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    return [
        p
        for p in screenshots_dir.iterdir()
        if p.is_file() and p.suffix.lower() in valid_exts
    ]


def should_trigger_downloads_triage(
    store: SQLiteStore,
    cadence_days: int = 5,
) -> bool:
    """Check if downloads triage cadence has elapsed."""
    item = store.get(DAEMON_NAMESPACE, DOWNLOADS_LAST_TRIGGER_KEY)
    now_ts = time.time()
    if item is None:
        return True

    last_ts = item.value.get("timestamp", 0)
    seconds_threshold = cadence_days * 86400
    return (now_ts - last_ts) >= seconds_threshold


def record_downloads_triage_run(store: SQLiteStore) -> None:
    """Update last run timestamp for downloads triage in store."""
    store.put(
        DAEMON_NAMESPACE,
        DOWNLOADS_LAST_TRIGGER_KEY,
        {"timestamp": time.time(), "recorded_at": datetime.now(timezone.utc).isoformat()},
    )


class DaemonCycleResult:
    """Summary of checks executed during a daemon polling iteration."""

    def __init__(
        self,
        screenshots_count: int = 0,
        screenshot_triggered: bool = False,
        downloads_triggered: bool = False,
    ):
        self.screenshots_count = screenshots_count
        self.screenshot_triggered = screenshot_triggered
        self.downloads_triggered = downloads_triggered


async def run_daemon_tick(
    settings: Settings,
    store: SQLiteStore,
    screenshot_threshold: int = 10,
    downloads_cadence_days: int = 5,
    send_notifications: bool = True,
) -> DaemonCycleResult:
    """Perform a single daemon check cycle across screenshots and downloads."""
    result = DaemonCycleResult()

    # 1. Inspect screenshots directory
    screenshots_dir = settings.screenshots.get_resolved_directory()
    eligible_screens = count_eligible_screenshots(screenshots_dir)
    result.screenshots_count = len(eligible_screens)

    if result.screenshots_count >= screenshot_threshold:
        result.screenshot_triggered = True
        if send_notifications:
            send_windows_toast(
                title="DeskPilot Triage Alert",
                message=f"Accumulated {result.screenshots_count} screenshots. Run DeskPilot triage to organize them into Notion/archive.",
            )

    # 2. Inspect downloads cadence
    if should_trigger_downloads_triage(store, cadence_days=downloads_cadence_days):
        result.downloads_triggered = True
        if send_notifications:
            send_windows_toast(
                title="DeskPilot Downloads Maintenance",
                message="It's been 5 days since your last downloads triage. Run DeskPilot to organize recent files.",
            )
        record_downloads_triage_run(store)

    return result


async def run_daemon_loop(
    settings: Settings,
    store: SQLiteStore,
    poll_interval_seconds: int = 300,
    screenshot_threshold: int = 10,
    downloads_cadence_days: int = 5,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Continuous background loop executing daemon checks."""
    while True:
        if stop_event is not None and stop_event.is_set():
            break
        await run_daemon_tick(
            settings,
            store,
            screenshot_threshold=screenshot_threshold,
            downloads_cadence_days=downloads_cadence_days,
            send_notifications=True,
        )
        try:
            if stop_event is not None:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_seconds)
                break
            else:
                await asyncio.sleep(poll_interval_seconds)
        except asyncio.TimeoutError:
            pass
