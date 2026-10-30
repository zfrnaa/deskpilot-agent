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

    # First check: never run before -> should trigger
    assert should_trigger_downloads_triage(store, cadence_days=5) is True

    # Record run
    record_downloads_triage_run(store)

    # Immediately check again -> should NOT trigger
    assert should_trigger_downloads_triage(store, cadence_days=5) is False

    # Simulate 6 days past
    from deskpilot.daemon import DAEMON_NAMESPACE, DOWNLOADS_LAST_TRIGGER_KEY
    six_days_ago = time.time() - (6 * 86400)
    store.put(DAEMON_NAMESPACE, DOWNLOADS_LAST_TRIGGER_KEY, {"timestamp": six_days_ago})

    assert should_trigger_downloads_triage(store, cadence_days=5) is True


@pytest.mark.asyncio
async def test_run_daemon_tick(tmp_path: Path):
    screenshots_dir = tmp_path / "Screenshots"
    screenshots_dir.mkdir()
    for i in range(12):
        (screenshots_dir / f"screen_{i}.png").write_text("img")

    settings = Settings()
    settings.screenshots.directory = screenshots_dir

    db_file = tmp_path / "daemon_tick_store.db"
    store = SQLiteStore(db_path=db_file)

    with patch("deskpilot.daemon.send_windows_toast") as mock_toast:
        res = await run_daemon_tick(
            settings=settings,
            store=store,
            screenshot_threshold=10,
            downloads_cadence_days=5,
            send_notifications=True,
        )

        assert res.screenshots_count == 12
        assert res.screenshot_triggered is True
        assert res.downloads_triggered is True
        assert mock_toast.call_count == 2
