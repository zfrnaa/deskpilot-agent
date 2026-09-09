"""Tests for DeskPilot Screenshot Triage Agent with LangGraph & Notion."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from deskpilot.config import NotionConfig, ScreenshotsConfig, Settings
from deskpilot.agent_tasks.screenshot_agent.graph import (
    build_screenshot_triage_graph,
    cleanup_synced,
    cluster_items,
    human_review_node,
    notion_sync,
    run_screenshot_triage,
    scan_screenshots,
    vision_triage,
)
from deskpilot.agent_tasks.screenshot_agent.notion_sync import (
    cleanup_synced_files,
    sync_approved_items,
    sync_screenshot_to_notion,
)
from deskpilot.agent_tasks.screenshot_agent.state import ScreenshotAgentState, ScreenshotItem
from deskpilot.agent_tasks.screenshot_agent.vision import (
    classify_screenshot,
    encode_image_to_base64,
    triage_screenshots,
)


def _create_dummy_image(path: Path, color: str = "blue", size: tuple[int, int] = (100, 100)) -> Path:
    """Helper to create a valid dummy image file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", size, color=color)
    img.save(path)
    return path


def test_screenshot_item_model_defaults_and_validation(tmp_path: Path):
    """Verify ScreenshotItem default field values and filename population."""
    img_file = tmp_path / "receipt_2026.png"
    item = ScreenshotItem(path=img_file)

    assert item.path == img_file
    assert item.filename == "receipt_2026.png"
    assert item.title == ""
    assert item.classification == "LOCAL_KEEP"
    assert item.cluster_tag == "General"
    assert item.rationale == ""
    assert item.is_synced is False
    assert item.deleted_locally is False


def test_scan_screenshots_finds_images_and_respects_max(tmp_path: Path):
    """Verify scan_screenshots discovers only image files and enforces max_images limit."""
    img1 = _create_dummy_image(tmp_path / "shot1.png")
    img2 = _create_dummy_image(tmp_path / "shot2.jpg")
    img3 = _create_dummy_image(tmp_path / "shot3.jpeg")
    # Non-image files should be ignored
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "data.pdf").write_bytes(b"%PDF-1.4")

    state: ScreenshotAgentState = {
        "screenshots_dir": tmp_path,
        "max_images": 2,
        "items": [],
        "clusters": {},
        "approved_cluster_keys": [],
        "synced_count": 0,
        "deleted_count": 0,
        "errors": [],
    }

    result = scan_screenshots(state)
    found_items = result["items"]

    assert len(found_items) == 2
    for item in found_items:
        assert isinstance(item, ScreenshotItem)
        assert item.path.suffix.lower() in {".png", ".jpg", ".jpeg"}


def test_scan_screenshots_handles_missing_directory_gracefully(tmp_path: Path):
    """Verify scanning non-existent directory logs error without crashing."""
    missing_dir = tmp_path / "non_existent_folder"
    state: ScreenshotAgentState = {
        "screenshots_dir": missing_dir,
        "max_images": 10,
        "items": [],
        "clusters": {},
        "approved_cluster_keys": [],
        "synced_count": 0,
        "deleted_count": 0,
        "errors": [],
    }

    result = scan_screenshots(state)
    assert result["items"] == []
    assert len(result["errors"]) >= 1
    assert "does not exist" in result["errors"][0]


def test_encode_image_to_base64_valid_image(tmp_path: Path):
    """Verify base64 encoding returns valid non-empty string."""
    img_path = _create_dummy_image(tmp_path / "sample.png")
    b64_str = encode_image_to_base64(img_path)
    assert isinstance(b64_str, str)
    assert len(b64_str) > 0


def test_classify_screenshot_with_mock_llm(tmp_path: Path):
    """Verify vision classification correctly parses LLM response into ScreenshotItem."""
    img_path = _create_dummy_image(tmp_path / "code_architecture.png")
    item = ScreenshotItem(path=img_path)

    mock_response = MagicMock()
    mock_response.content = json.dumps({
        "classification": "NOTION_NOTE",
        "title": "System Architecture Overview",
        "cluster_tag": "Engineering",
        "rationale": "High-level diagram of distributed agents",
    })

    mock_llm = MagicMock()
    mock_llm.invoke.return_value = mock_response

    updated = classify_screenshot(item, llm=mock_llm)
    assert updated.classification == "NOTION_NOTE"
    assert updated.title == "System Architecture Overview"
    assert updated.cluster_tag == "Engineering"
    assert "distributed agents" in updated.rationale


def test_classify_screenshot_handles_llm_failure_safely(tmp_path: Path):
    """Verify LLM error or invalid JSON safely falls back to LOCAL_KEEP."""
    img_path = _create_dummy_image(tmp_path / "error_shot.png")
    item = ScreenshotItem(path=img_path)

    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = RuntimeError("API quota exceeded")

    updated = classify_screenshot(item, llm=mock_llm)
    assert updated.classification == "LOCAL_KEEP"
    assert updated.cluster_tag == "General"
    assert "error" in updated.rationale.lower()


def test_clustering_groups_items_by_tag(tmp_path: Path):
    """Verify cluster_items node partitions ScreenshotItems into cluster dictionary."""
    i1 = ScreenshotItem(path=tmp_path / "a.png", cluster_tag="Work", classification="NOTION_NOTE")
    i2 = ScreenshotItem(path=tmp_path / "b.png", cluster_tag="Work", classification="NOTION_NOTE")
    i3 = ScreenshotItem(path=tmp_path / "c.png", cluster_tag="Receipts", classification="NOTION_NOTE")
    i4 = ScreenshotItem(path=tmp_path / "d.png", cluster_tag="Personal", classification="LOCAL_KEEP")

    state: ScreenshotAgentState = {
        "screenshots_dir": tmp_path,
        "max_images": 10,
        "items": [i1, i2, i3, i4],
        "clusters": {},
        "approved_cluster_keys": [],
        "synced_count": 0,
        "deleted_count": 0,
        "errors": [],
    }

    result = cluster_items(state)
    clusters = result["clusters"]

    assert set(clusters.keys()) == {"Work", "Receipts", "Personal"}
    assert len(clusters["Work"]) == 2
    assert len(clusters["Receipts"]) == 1
    assert len(clusters["Personal"]) == 1


def test_human_review_node_auto_approve_behavior(tmp_path: Path):
    """Verify human_review_node approves clusters containing NOTION_NOTE items in auto mode."""
    i1 = ScreenshotItem(path=tmp_path / "a.png", cluster_tag="Work", classification="NOTION_NOTE")
    i2 = ScreenshotItem(path=tmp_path / "b.png", cluster_tag="Random", classification="LOCAL_KEEP")

    state: ScreenshotAgentState = {
        "screenshots_dir": tmp_path,
        "max_images": 10,
        "items": [i1, i2],
        "clusters": {"Work": [i1], "Random": [i2]},
        "approved_cluster_keys": [],
        "synced_count": 0,
        "deleted_count": 0,
        "errors": [],
        "auto_approve": True,
    }

    result = human_review_node(state)
    assert "Work" in result["approved_cluster_keys"]


def test_sync_screenshot_to_notion_success(tmp_path: Path):
    """Verify successful Notion sync updates is_synced to True."""
    img_path = _create_dummy_image(tmp_path / "note1.png")
    item = ScreenshotItem(
        path=img_path,
        title="Quick API Doc",
        classification="NOTION_NOTE",
        cluster_tag="Docs",
        rationale="API endpoint reference",
    )

    mock_client = MagicMock()
    mock_client.pages.create.return_value = {"id": "page_456"}

    success = sync_screenshot_to_notion(
        item=item,
        notion_client=mock_client,
        parent_page_id="test_parent_id",
    )

    assert success is True
    assert item.is_synced is True
    mock_client.pages.create.assert_called_once()


def test_sync_screenshot_to_notion_failure_leaves_unsynced(tmp_path: Path):
    """Verify failed Notion sync leaves is_synced False without raising unhandled exception."""
    img_path = _create_dummy_image(tmp_path / "note2.png")
    item = ScreenshotItem(
        path=img_path,
        title="Failed Upload",
        classification="NOTION_NOTE",
        cluster_tag="Docs",
    )

    mock_client = MagicMock()
    mock_client.pages.create.side_effect = ConnectionError("Notion API unreachable")

    success = sync_screenshot_to_notion(
        item=item,
        notion_client=mock_client,
        parent_page_id="test_parent_id",
    )

    assert success is False
    assert item.is_synced is False


def test_safety_guarantee_cleanup_never_deletes_unsynced_or_local_keep(tmp_path: Path):
    """CRITICAL SAFETY TEST: Verify non-synced and LOCAL_KEEP screenshots are NEVER deleted."""
    keep_file = _create_dummy_image(tmp_path / "keep_local.png")
    failed_sync_file = _create_dummy_image(tmp_path / "failed_sync.png")
    unapproved_file = _create_dummy_image(tmp_path / "unapproved.png")

    i_keep = ScreenshotItem(path=keep_file, classification="LOCAL_KEEP", is_synced=False)
    i_failed = ScreenshotItem(path=failed_sync_file, classification="NOTION_NOTE", is_synced=False)
    i_unapproved = ScreenshotItem(path=unapproved_file, classification="NOTION_NOTE", is_synced=False)

    deleted_count, errors = cleanup_synced_files([i_keep, i_failed, i_unapproved], delete_synced_local=True)

    assert deleted_count == 0
    assert keep_file.exists(), "LOCAL_KEEP file must remain on disk!"
    assert failed_sync_file.exists(), "Failed-sync file must remain on disk!"
    assert unapproved_file.exists(), "Unapproved file must remain on disk!"
    assert not i_keep.deleted_locally
    assert not i_failed.deleted_locally
    assert not i_unapproved.deleted_locally


def test_cleanup_synced_files_deletes_only_verified_synced(tmp_path: Path):
    """Verify only items with is_synced=True and NOTION_NOTE are deleted when configured."""
    synced_file = _create_dummy_image(tmp_path / "synced_receipt.png")
    keep_file = _create_dummy_image(tmp_path / "keep.png")

    i_synced = ScreenshotItem(path=synced_file, classification="NOTION_NOTE", is_synced=True)
    i_keep = ScreenshotItem(path=keep_file, classification="LOCAL_KEEP", is_synced=False)

    deleted_count, errors = cleanup_synced_files([i_synced, i_keep], delete_synced_local=True)

    assert deleted_count == 1
    assert not synced_file.exists(), "Synced file should be deleted locally"
    assert i_synced.deleted_locally is True
    assert keep_file.exists(), "LOCAL_KEEP file must remain on disk"
    assert i_keep.deleted_locally is False


def test_cleanup_synced_files_preserves_when_delete_synced_local_disabled(tmp_path: Path):
    """Verify no files are unlinked if delete_synced_local is set to False."""
    synced_file = _create_dummy_image(tmp_path / "synced_doc.png")
    i_synced = ScreenshotItem(path=synced_file, classification="NOTION_NOTE", is_synced=True)

    deleted_count, errors = cleanup_synced_files([i_synced], delete_synced_local=False)

    assert deleted_count == 0
    assert synced_file.exists(), "File must not be deleted when delete_synced_local=False"
    assert i_synced.deleted_locally is False


@pytest.mark.asyncio
async def test_end_to_end_graph_execution(tmp_path: Path):
    """Verify entire LangGraph triage workflow executes with mocked vision & Notion."""
    shot_note = _create_dummy_image(tmp_path / "recipe.png")
    shot_keep = _create_dummy_image(tmp_path / "gameplay.png")

    def mock_vision_callable(item: ScreenshotItem) -> ScreenshotItem:
        if "recipe" in item.filename:
            item.classification = "NOTION_NOTE"
            item.title = "Chocolate Cake Recipe"
            item.cluster_tag = "Recipes"
            item.rationale = "Useful baking recipe"
        else:
            item.classification = "LOCAL_KEEP"
            item.title = "Game Screenshot"
            item.cluster_tag = "Gaming"
            item.rationale = "Personal gaming moment"
        return item

    mock_notion = MagicMock()
    mock_notion.pages.create.return_value = {"id": "new_page_id"}

    graph = build_screenshot_triage_graph(
        llm=mock_vision_callable,
        notion_client=mock_notion,
        parent_page_id="test_page_123",
        delete_synced_local=True,
    )

    initial_state: ScreenshotAgentState = {
        "screenshots_dir": tmp_path,
        "max_images": 10,
        "items": [],
        "clusters": {},
        "approved_cluster_keys": [],
        "synced_count": 0,
        "deleted_count": 0,
        "errors": [],
        "auto_approve": True,
        "delete_synced_local": True,
    }

    final_state = await graph.ainvoke(initial_state)

    assert len(final_state["items"]) == 2
    assert final_state["synced_count"] == 1
    assert final_state["deleted_count"] == 1
    assert not shot_note.exists(), "Synced note should be unlinked"
    assert shot_keep.exists(), "LOCAL_KEEP file must remain intact"


@pytest.mark.asyncio
async def test_run_screenshot_triage_orchestration(tmp_path: Path):
    """Verify run_screenshot_triage top-level helper initializes state and runs graph."""
    shot = _create_dummy_image(tmp_path / "meeting_notes.png")

    settings = Settings(
        screenshots=ScreenshotsConfig(directory=tmp_path, delete_synced_local=True),
        notion=NotionConfig(token="mock_token", parent_page_id="mock_parent_id"),
    )

    def mock_vision(item: ScreenshotItem) -> ScreenshotItem:
        item.classification = "NOTION_NOTE"
        item.title = "Sprint Planning"
        item.cluster_tag = "Work"
        return item

    mock_notion = MagicMock()
    mock_notion.pages.create.return_value = {"id": "page_789"}

    result_state = await run_screenshot_triage(
        settings=settings,
        auto_approve=True,
        llm=mock_vision,
        notion_client=mock_notion,
    )

    assert result_state["synced_count"] == 1
    assert result_state["deleted_count"] == 1
    assert not shot.exists()


def test_no_utf8_bom_in_screenshot_agent_files():
    """Verify all Python files in agent_tasks and test files have no UTF-8 BOM."""
    base_dir = Path(__file__).resolve().parent.parent
    paths_to_check = [
        base_dir / "tests" / "test_screenshot_agent.py",
        base_dir / "deskpilot" / "agent_tasks" / "__init__.py",
        base_dir / "deskpilot" / "agent_tasks" / "screenshot_agent" / "__init__.py",
        base_dir / "deskpilot" / "agent_tasks" / "screenshot_agent" / "state.py",
        base_dir / "deskpilot" / "agent_tasks" / "screenshot_agent" / "vision.py",
        base_dir / "deskpilot" / "agent_tasks" / "screenshot_agent" / "notion_sync.py",
        base_dir / "deskpilot" / "agent_tasks" / "screenshot_agent" / "graph.py",
    ]

    for p in paths_to_check:
        if p.exists():
            content = p.read_bytes()
            assert not content.startswith(b"\xef\xbb\xbf"), f"File {p} starts with UTF-8 BOM"


def test_lazy_loading_of_langgraph_and_llm_libraries():
    """Verify boot_tasks and cli imports do not eagerly import langgraph or langchain."""
    import subprocess
    import sys

    code = (
        "import sys; "
        "import deskpilot.cli; "
        "import deskpilot.boot_tasks; "
        "assert 'langgraph' not in sys.modules, 'langgraph eagerly imported!'; "
        "assert 'langchain' not in sys.modules, 'langchain eagerly imported!'"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, f"Lazy loading check failed: {result.stderr}"

