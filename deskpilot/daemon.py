"""Background autonomous watcher daemon for DeskPilot."""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from deskpilot.boot_tasks.memory_manager import (
    detect_active_game,
    get_system_memory_status,
    perform_game_standby_purge,
    perform_periodic_memory_cleanup,
)
from deskpilot.config import Settings
from deskpilot.storage.store import SQLiteStore
from deskpilot.ui.notifications import send_windows_toast


DAEMON_NAMESPACE = ("daemon", "cadence")
DOWNLOADS_LAST_TRIGGER_KEY = "downloads_last_triage_ts"
MONTHLY_HYGIENE_LAST_TRIGGER_KEY = "monthly_hygiene_last_run_ts"
MEMORY_CLEANUP_LAST_TRIGGER_KEY = "memory_cleanup_last_run_ts"
GAME_PREVIOUS_ACTIVE_KEY = "game_previous_active_name"


def should_trigger_monthly_hygiene(
    store: SQLiteStore,
    cadence_days: int = 30,
) -> bool:
    """Check if the 30-day monthly SystemCare hygiene cadence has elapsed."""
    item = store.get(DAEMON_NAMESPACE, MONTHLY_HYGIENE_LAST_TRIGGER_KEY)
    now_ts = time.time()
    if item is None:
        return True

    last_ts = item.value.get("timestamp", 0)
    seconds_threshold = cadence_days * 86400
    return (now_ts - last_ts) >= seconds_threshold


def record_monthly_hygiene_run(store: SQLiteStore) -> None:
    """Record timestamp of the monthly SystemCare deep hygiene execution."""
    store.put(
        DAEMON_NAMESPACE,
        MONTHLY_HYGIENE_LAST_TRIGGER_KEY,
        {"timestamp": time.time(), "recorded_at": datetime.now(timezone.utc).isoformat()},
    )


def should_trigger_periodic_memory_clean(
    store: SQLiteStore,
    cadence_minutes: int = 30,
    memory_load_threshold: int = 75,
) -> bool:
    """Check if periodic memory cleanup is due (by time or high memory load)."""
    # 1. High memory load threshold check
    mem_stat = get_system_memory_status()
    if mem_stat.memory_load_percent >= memory_load_threshold:
        return True

    # 2. Time cadence check
    item = store.get(DAEMON_NAMESPACE, MEMORY_CLEANUP_LAST_TRIGGER_KEY)
    now_ts = time.time()
    if item is None:
        return True

    last_ts = item.value.get("timestamp", 0)
    seconds_threshold = cadence_minutes * 60
    return (now_ts - last_ts) >= seconds_threshold


def record_memory_cleanup_run(store: SQLiteStore) -> None:
    """Update last run timestamp for periodic memory cleanup."""
    store.put(
        DAEMON_NAMESPACE,
        MEMORY_CLEANUP_LAST_TRIGGER_KEY,
        {"timestamp": time.time(), "recorded_at": datetime.now(timezone.utc).isoformat()},
    )


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
        game_standby_purged: bool = False,
        memory_cleanup_triggered: bool = False,
        active_game: str | None = None,
    ):
        self.screenshots_count = screenshots_count
        self.screenshot_triggered = screenshot_triggered
        self.downloads_triggered = downloads_triggered
        self.game_standby_purged = game_standby_purged
        self.memory_cleanup_triggered = memory_cleanup_triggered
        self.active_game = active_game


async def run_daemon_tick(
    settings: Settings,
    store: SQLiteStore,
    screenshot_threshold: int = 10,
    downloads_cadence_days: int = 5,
    send_notifications: bool = True,
) -> DaemonCycleResult:
    """Perform a single daemon check cycle across screenshots, downloads, and memory/game state."""
    result = DaemonCycleResult()

    # 1. Memory Management & Game Launch Standby Purge
    mem_cfg = getattr(settings, "memory", None)
    if mem_cfg and mem_cfg.enabled:
        active_game = detect_active_game(mem_cfg.game_processes)
        result.active_game = active_game

        prev_game_item = store.get(DAEMON_NAMESPACE, GAME_PREVIOUS_ACTIVE_KEY)
        prev_game = prev_game_item.value.get("name") if prev_game_item else None

        # Game just launched (newly detected in this tick)
        if active_game and active_game != prev_game:
            if mem_cfg.standby_purge_on_game:
                clean_res = perform_game_standby_purge(mem_cfg.game_processes)
                result.game_standby_purged = clean_res.standby_purged
                if send_notifications:
                    send_windows_toast(
                        title="DeskPilot Gaming Optimization",
                        message=f"{active_game.upper()} detected. Purged Windows Standby RAM cache for smooth gameplay.",
                        action_command="deskpilot://open",
                    )
            store.put(DAEMON_NAMESPACE, GAME_PREVIOUS_ACTIVE_KEY, {"name": active_game})
        elif not active_game and prev_game:
            # Game was closed
            store.put(DAEMON_NAMESPACE, GAME_PREVIOUS_ACTIVE_KEY, {"name": None})

        # Periodic Memory Maintenance (ONLY when NOT gaming!)
        if not active_game and mem_cfg.periodic_cleanup_enabled:
            if should_trigger_periodic_memory_clean(
                store,
                cadence_minutes=mem_cfg.periodic_interval_minutes,
                memory_load_threshold=mem_cfg.high_memory_load_threshold_percent,
            ):
                perform_periodic_memory_cleanup(mem_cfg.game_processes, force_even_if_gaming=False)
                record_memory_cleanup_run(store)
                result.memory_cleanup_triggered = True

    # 2. Inspect screenshots directory
    screenshots_dir = settings.screenshots.get_resolved_directory()
    eligible_screens = count_eligible_screenshots(screenshots_dir)
    result.screenshots_count = len(eligible_screens)

    if result.screenshots_count >= screenshot_threshold:
        result.screenshot_triggered = True
        if send_notifications:
            send_windows_toast(
                title="DeskPilot Triage Alert",
                message=f"Accumulated {result.screenshots_count} screenshots. Run DeskPilot triage to organize them into Notion/archive.",
                action_command="deskpilot://open",
            )

    # 3. Inspect downloads cadence
    if should_trigger_downloads_triage(store, cadence_days=downloads_cadence_days):
        result.downloads_triggered = True
        if send_notifications:
            send_windows_toast(
                title="DeskPilot Downloads Maintenance",
                message=f"It's been {downloads_cadence_days} days since your last downloads triage. Run DeskPilot to organize recent files.",
                action_command="deskpilot://open",
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
