"""Unit and integration tests for local screenshot organization & semantic renaming."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from deskpilot.agent_tasks.screenshot_agent.organizer import (
    DEFAULT_CATEGORY_FOLDERS,
    execute_local_organization,
    generate_semantic_filename,
    plan_local_organization,
    slugify_description,
)
from deskpilot.agent_tasks.screenshot_agent.state import ScreenshotItem
from deskpilot.agent_tasks.screenshot_agent.graph import build_screenshot_triage_graph


def test_slugify_description():
    assert slugify_description("Fix broken CI pipeline for tests") == "fix-broken-ci-pipeline-for"
    assert slugify_description("Special @#$ Characters!! In Title") == "special-characters-in-title"
    assert slugify_description("") == "screenshot"
    assert slugify_description("   ---   ") == "screenshot"


def test_generate_semantic_filename_format(tmp_path: Path):
    test_img = tmp_path / "screenshot_123.png"
    test_img.write_text("fake image content")

    item = ScreenshotItem(
        path=test_img,
        title="Dashboard Architecture Diagram",
        cluster_tag="Architecture",
        classification="WORK_NOTES",
    )

    fname = generate_semantic_filename(item)
    assert fname.endswith(".png")
    # Must follow YYYY-MM-DD_<short-description>.png
    parts = fname.split("_", 1)
    assert len(parts) == 2
    assert len(parts[0]) == 10  # YYYY-MM-DD
    assert "dashboard-architecture" in parts[1]


def test_plan_local_organization_skip_deleted_and_synced(tmp_path: Path):
    s_dir = tmp_path / "Screenshots"
    s_dir.mkdir()

    img1 = s_dir / "img1.png"
    img1.write_text("1")
    img2 = s_dir / "img2.png"
    img2.write_text("2")
    img3 = s_dir / "img3.png"
    img3.write_text("3")

    item_deleted = ScreenshotItem(
        path=img1,
        title="Deleted Item",
        is_synced=True,
        deleted_locally=True,
        classification="WORK_NOTES",
    )
    item_synced_unlinked = ScreenshotItem(
        path=img2,
        title="Synced Item To Unlink",
        is_synced=True,
        image_synced=True,
        classification="WORK_NOTES",
    )
    item_keep = ScreenshotItem(
        path=img3,
        title="Keep Locally",
        classification="LOCAL_KEEP",
    )

    items = plan_local_organization(
        [item_deleted, item_synced_unlinked, item_keep],
        screenshots_dir=s_dir,
        delete_synced_local=True,
    )

    assert items[0].target_path is None
    assert items[1].target_path is None
    assert items[2].target_path is not None
    assert "General" in str(items[2].target_path)


def test_plan_local_organization_keep_synced_when_delete_false(tmp_path: Path):
    s_dir = tmp_path / "Screenshots"
    s_dir.mkdir()

    img = s_dir / "work.png"
    img.write_text("work")

    item_synced = ScreenshotItem(
        path=img,
        title="Work Project Roadmap",
        is_synced=True,
        classification="WORK_NOTES",
    )

    items = plan_local_organization(
        [item_synced],
        screenshots_dir=s_dir,
        delete_synced_local=False,
    )

    assert items[0].target_path is not None
    assert "Work Notes" in str(items[0].target_path)


def test_execute_local_organization_moves_and_handles_collisions(tmp_path: Path):
    s_dir = tmp_path / "Screenshots"
    s_dir.mkdir()

    src1 = s_dir / "capture1.png"
    src1.write_text("content1")
    src2 = s_dir / "capture2.png"
    src2.write_text("content2")

    item1 = ScreenshotItem(path=src1, title="Meeting Notes", classification="WORK_NOTES")
    item2 = ScreenshotItem(path=src2, title="Meeting Notes", classification="WORK_NOTES")

    items = plan_local_organization([item1, item2], screenshots_dir=s_dir)
    assert items[0].target_path != items[1].target_path

    count, errors = execute_local_organization(items)
    assert count == 2
    assert len(errors) == 0
    assert not src1.exists()
    assert not src2.exists()
    assert items[0].path.exists()
    assert items[1].path.exists()
    assert items[0].organized_locally is True
    assert items[1].organized_locally is True


@pytest.mark.asyncio
async def test_screenshot_triage_graph_with_local_organization(tmp_path: Path):
    s_dir = tmp_path / "Screenshots"
    s_dir.mkdir()

    img = s_dir / "snip.png"
    img.write_text("test")

    def mock_vision(item):
        item.classification = "LOCAL_KEEP"
        item.title = "Personal Game High Score"
        item.cluster_tag = "Gaming"
        return item

    graph = build_screenshot_triage_graph(
        llm=mock_vision,
        auto_approve=True,
        organize_local=True,
    )

    state = {
        "screenshots_dir": s_dir,
        "max_images": 10,
        "auto_approve": True,
        "organize_local": True,
        "delete_synced_local": True,
    }

    result = await graph.ainvoke(state)
    assert result.get("organized_count") == 1
    assert not img.exists()

    gen_dir = s_dir / "General"
    assert gen_dir.exists()
    files = list(gen_dir.iterdir())
    assert len(files) == 1
    assert "personal-game" in files[0].name


def test_plan_local_organization_organizes_text_only_synced_item(tmp_path: Path):
    """Items with is_synced=True but image_synced=False are preserved locally and must be organized."""
    s_dir = tmp_path / "Screenshots"
    s_dir.mkdir()
    img = s_dir / "text_only_synced.png"
    img.write_text("img")

    item = ScreenshotItem(
        path=img,
        title="Text Only Synced Note",
        is_synced=True,
        image_synced=False,
        deleted_locally=False,
        classification="WORK_NOTES",
    )

    items = plan_local_organization([item], screenshots_dir=s_dir, delete_synced_local=True)
    assert items[0].target_path is not None
    assert "Work Notes" in str(items[0].target_path)


def test_plan_local_organization_blocks_path_traversal(tmp_path: Path):
    """category_folders with '..' or absolute paths must not escape screenshots_dir."""
    s_dir = tmp_path / "Screenshots"
    s_dir.mkdir()
    img1 = s_dir / "escape1.png"
    img1.write_text("1")
    img2 = s_dir / "escape2.png"
    img2.write_text("2")

    item_rel = ScreenshotItem(path=img1, title="Rel Escape", classification="WORK_NOTES")
    item_abs = ScreenshotItem(path=img2, title="Abs Escape", classification="BRAINSTORM")

    bad_folders = {
        "WORK_NOTES": "../../outside",
        "BRAINSTORM": str((tmp_path / "other_abs_dir").resolve()),
    }

    items = plan_local_organization(
        [item_rel, item_abs],
        screenshots_dir=s_dir,
        category_folders=bad_folders,
    )
    assert items[0].target_path is None
    assert items[1].target_path is None
