from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from deskpilot.config import Settings
from deskpilot.daemon import (
    DAEMON_NAMESPACE,
    MEMORY_CLEANUP_LAST_TRIGGER_KEY,
    SCREENSHOTS_LAST_ALERT_KEY,
    count_eligible_downloads,
    count_eligible_screenshots,
    record_downloads_triage_run,
    record_memory_cleanup_run,
    record_screenshot_alert,
    run_daemon_tick,
    should_trigger_downloads_triage,
    should_trigger_periodic_memory_clean,
    should_trigger_screenshot_alert,
)
from deskpilot.storage.store import SQLiteStore


def test_count_eligible_screenshots(tmp_path: Path):
    screenshots_dir = tmp_path / "Screenshots"
    screenshots_dir.mkdir()
    (screenshots_dir / "shot1.png").write_text("dummy")
    (screenshots_dir / "shot2.jpg").write_text("dummy")
    (screenshots_dir / "shot3.webp").write_text("dummy")
    (screenshots_dir / "shot4.bmp").write_text("dummy")
    (screenshots_dir / ".hidden.png").write_text("hidden")
    (screenshots_dir / "~temp.png").write_text("temp")
    (screenshots_dir / "not_an_image.txt").write_text("text")

    res = count_eligible_screenshots(screenshots_dir)
    assert len(res) == 4
    filenames = {p.name for p in res}
    assert {"shot1.png", "shot2.jpg", "shot3.webp", "shot4.bmp"} == filenames


def test_count_eligible_downloads(tmp_path: Path):
    downloads_dir = tmp_path / "Downloads"
    downloads_dir.mkdir()
    assert count_eligible_downloads(downloads_dir) == []

    (downloads_dir / "setup.exe").write_text("bin")
    (downloads_dir / "archive.zip").write_text("zip")
    (downloads_dir / ".hidden").write_text("hidden")
    (downloads_dir / "SubDir").mkdir()

    res = count_eligible_downloads(downloads_dir)
    assert len(res) == 2
    filenames = {p.name for p in res}
    assert "setup.exe" in filenames
    assert "archive.zip" in filenames
    assert ".hidden" not in filenames


def test_downloads_cadence_trigger(tmp_path: Path):
    db_file = tmp_path / "test_store.db"
    store = SQLiteStore(db_path=db_file)

    assert should_trigger_downloads_triage(store, cadence_days=5) is True
    record_downloads_triage_run(store)
    assert should_trigger_downloads_triage(store, cadence_days=5) is False

    from deskpilot.daemon import DAEMON_NAMESPACE, DOWNLOADS_LAST_TRIGGER_KEY
    six_days_ago = time.time() - (6 * 86400)
    store.put(DAEMON_NAMESPACE, DOWNLOADS_LAST_TRIGGER_KEY, {"timestamp": six_days_ago})

    assert should_trigger_downloads_triage(store, cadence_days=5) is True


@pytest.mark.asyncio
async def test_run_daemon_tick_without_game(tmp_path: Path):
    screenshots_dir = tmp_path / "Screenshots"
    screenshots_dir.mkdir()
    for i in range(12):
        (screenshots_dir / f"screen_{i}.png").write_text("img")

    settings = Settings()
    settings.screenshots.directory = screenshots_dir

    db_file = tmp_path / "daemon_tick_store.db"
    store = SQLiteStore(db_path=db_file)
    record_downloads_triage_run(store)  # Initialize downloads last run

    with patch("deskpilot.daemon.detect_active_game", return_value=None), \
         patch("deskpilot.daemon.send_windows_toast") as mock_toast:
        res = await run_daemon_tick(
            settings=settings,
            store=store,
            screenshot_threshold=10,
            downloads_cadence_days=5,
            send_notifications=True,
        )

        assert res.screenshots_count == 12
        assert res.screenshot_triggered is True
        assert res.downloads_triggered is False
        assert res.active_game is None
        assert mock_toast.call_count == 1


@pytest.mark.asyncio
async def test_run_daemon_tick_game_launch_triggers_standby_purge(tmp_path: Path):
    settings = Settings()
    db_file = tmp_path / "game_daemon_store.db"
    store = SQLiteStore(db_path=db_file)
    record_downloads_triage_run(store)  # Initialize downloads last run

    with patch("deskpilot.daemon.detect_active_game", return_value="tslgame.exe"), \
         patch("deskpilot.daemon.perform_game_standby_purge") as mock_purge, \
         patch("deskpilot.daemon.send_windows_toast") as mock_toast:
        
        mock_purge.return_value = MagicMock(standby_purged=True)

        res = await run_daemon_tick(
            settings=settings,
            store=store,
            screenshot_threshold=100,
            downloads_cadence_days=100,
            send_notifications=True,
        )

        assert res.active_game == "tslgame.exe"
        assert res.game_standby_purged is True
        assert mock_purge.call_count == 1
        assert mock_toast.call_count == 1
        toast_args = mock_toast.call_args[1]
        assert "Gaming Optimization" in toast_args["title"]
        assert "TSLGAME.EXE" in toast_args["message"]


def test_screenshot_alert_cooldown(tmp_path: Path):
    store = SQLiteStore(db_path=tmp_path / "shots_cooldown.db")
    assert should_trigger_screenshot_alert(store, cooldown_seconds=3600) is True
    record_screenshot_alert(store)
    assert should_trigger_screenshot_alert(store, cooldown_seconds=3600) is False

    two_hours_ago = time.time() - 7200
    store.put(DAEMON_NAMESPACE, SCREENSHOTS_LAST_ALERT_KEY, {"timestamp": two_hours_ago})
    assert should_trigger_screenshot_alert(store, cooldown_seconds=3600) is True


def test_high_memory_cleanup_cooldown(tmp_path: Path):
    store = SQLiteStore(db_path=tmp_path / "mem_cooldown.db")
    high_mem = MagicMock(memory_load_percent=90)

    with patch("deskpilot.daemon.get_system_memory_status", return_value=high_mem):
        assert should_trigger_periodic_memory_clean(store, cadence_minutes=30, memory_load_threshold=75) is True
        record_memory_cleanup_run(store)
        assert should_trigger_periodic_memory_clean(store, cadence_minutes=30, memory_load_threshold=75) is False

        six_min_ago = time.time() - 360
        store.put(DAEMON_NAMESPACE, MEMORY_CLEANUP_LAST_TRIGGER_KEY, {"timestamp": six_min_ago})
        assert should_trigger_periodic_memory_clean(store, cadence_minutes=30, memory_load_threshold=75) is True


def test_sqlite_store_wal_and_busy_timeout(tmp_path: Path):
    store = SQLiteStore(db_path=tmp_path / "wal_check.db")
    conn = store._get_connection()
    try:
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
        assert str(mode).lower() == "wal"
        assert int(timeout) == 5000
    finally:
        conn.close()


def test_power_config_defaults():
    from deskpilot.config import Settings

    s = Settings()
    assert s.power.enabled is True
    assert s.power.gaming_plan == "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
    assert s.power.normal_plan == "381b4222-f694-41f0-9685-ff5bb260df2e"
