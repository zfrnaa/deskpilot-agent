from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from deskpilot.config import Settings
from deskpilot.daemon import (
    count_eligible_screenshots,
    should_trigger_downloads_triage,
    record_downloads_triage_run,
    run_daemon_tick,
)
from deskpilot.storage.store import SQLiteStore


def test_count_eligible_screenshots(tmp_path: Path):
    screenshots_dir = tmp_path / "Screenshots"
    screenshots_dir.mkdir()
    (screenshots_dir / "shot1.png").write_text("dummy")
    (screenshots_dir / "shot2.jpg").write_text("dummy")
    (screenshots_dir / "not_an_image.txt").write_text("text")

    res = count_eligible_screenshots(screenshots_dir)
    assert len(res) == 2
    filenames = {p.name for p in res}
    assert "shot1.png" in filenames
    assert "shot2.jpg" in filenames


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
