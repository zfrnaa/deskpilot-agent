"""Tests for DeskPilot Screenshot Triage Agent with LangGraph & Notion."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from deskpilot.config import NotionConfig, ScreenshotDestinationsConfig, ScreenshotsConfig, Settings
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
    introspect_database_schema,
    sync_approved_items,
    sync_screenshot_to_notion,
)
from deskpilot.agent_tasks.screenshot_agent.state import ScreenshotAgentState, ScreenshotItem
from deskpilot.agent_tasks.screenshot_agent.vision import (
    classify_screenshot,
    encode_image_to_base64,
    get_default_vision_llm,
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
        _env_file=None,
        screenshots=ScreenshotsConfig(directory=tmp_path, delete_synced_local=True),
        notion=NotionConfig(
            token="mock_token",
            parent_page_id="mock_parent_id",
            read_later_database_id="mock_read_later_db_id",
            screenshot_destinations=ScreenshotDestinationsConfig(work_notes_database_id=""),
        ),
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
    mock_notion.pages.create.assert_called_once()
    create_kwargs = mock_notion.pages.create.call_args[1]
    assert create_kwargs["parent"] == {"page_id": "mock_parent_id"}


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


def test_introspect_worknote_database_schema_and_dynamic_mapping(tmp_path: Path):
    """Verify introspecting WorkNote database schema dynamically maps title, select, and date properties."""
    img_path = _create_dummy_image(tmp_path / "work_arch.png")
    item = ScreenshotItem(
        path=img_path,
        title="Distributed Architecture Flow",
        classification="WORK_NOTES",
        cluster_tag="Engineering",
        rationale="System design chart",
    )

    mock_client = MagicMock()
    mock_client.databases.retrieve.return_value = {
        "id": "db_work_notes_123",
        "properties": {
            "Topic": {"id": "title", "name": "Topic", "type": "title", "title": {}},
            "Area": {"id": "area_id", "name": "Area", "type": "select", "select": {}},
            "Created": {"id": "date_id", "name": "Created", "type": "date", "date": {}},
        },
    }
    mock_client.pages.create.return_value = {"id": "page_work_001"}

    # Test standalone schema introspection
    schema = introspect_database_schema(mock_client, "db_work_notes_123")
    assert schema["title_prop"] == "Topic"
    assert schema["tag_prop"] == "Area"
    assert schema["tag_type"] == "select"
    assert schema["date_prop"] == "Created"

    destinations = ScreenshotDestinationsConfig(work_notes_database_id="db_work_notes_123")
    success = sync_screenshot_to_notion(
        item=item,
        notion_client=mock_client,
        destinations=destinations,
    )

    assert success is True
    assert item.is_synced is True
    mock_client.databases.retrieve.assert_called_with(database_id="db_work_notes_123")
    mock_client.pages.create.assert_called_once()
    create_kwargs = mock_client.pages.create.call_args[1]
    assert create_kwargs["parent"] == {"database_id": "db_work_notes_123"}
    props = create_kwargs["properties"]
    assert "Topic" in props
    assert props["Topic"]["title"][0]["text"]["content"] == "Distributed Architecture Flow"
    assert "Area" in props
    assert props["Area"]["select"]["name"] == "Engineering"
    assert "Created" in props
    assert "date" in props["Created"]


def test_introspect_worknote_database_schema_with_multiselect_tags(tmp_path: Path):
    """Verify dynamic mapping when database uses multi_select for tags."""
    img_path = _create_dummy_image(tmp_path / "work_multi.png")
    item = ScreenshotItem(
        path=img_path,
        title="Tag Test",
        classification="WORK_NOTES",
        cluster_tag="DevOps",
    )

    mock_client = MagicMock()
    mock_client.databases.retrieve.return_value = {
        "id": "db_work_tags",
        "properties": {
            "Name": {"id": "title", "type": "title", "title": {}},
            "Tags": {"id": "tag_id", "type": "multi_select", "multi_select": {}},
        },
    }
    mock_client.pages.create.return_value = {"id": "page_tag_002"}

    destinations = ScreenshotDestinationsConfig(work_notes_database_id="db_work_tags")
    success = sync_screenshot_to_notion(
        item=item,
        notion_client=mock_client,
        destinations=destinations,
    )

    assert success is True
    assert item.is_synced is True
    props = mock_client.pages.create.call_args[1]["properties"]
    assert "Tags" in props
    assert props["Tags"]["multi_select"][0]["name"] == "DevOps"


def test_multi_destination_routing_to_due_diligence_page(tmp_path: Path):
    """Verify DUE_DILIGENCE classification routes to due_diligence_page_id via blocks.children.append."""
    img_path = _create_dummy_image(tmp_path / "dd_survey.png")
    item = ScreenshotItem(
        path=img_path,
        title="Vendor Security Assessment",
        classification="DUE_DILIGENCE",
        cluster_tag="Security",
        rationale="SOC2 questionnaire table",
    )

    mock_client = MagicMock()
    mock_client.blocks.children.append.return_value = {"results": []}

    destinations = ScreenshotDestinationsConfig(due_diligence_page_id="page_dd_999")
    success = sync_screenshot_to_notion(
        item=item,
        notion_client=mock_client,
        destinations=destinations,
    )

    assert success is True
    assert item.is_synced is True
    mock_client.blocks.children.append.assert_called_once()
    call_args = mock_client.blocks.children.append.call_args[1]
    assert call_args["block_id"] == "page_dd_999"
    children = call_args["children"]
    assert any(b.get("type") == "callout" for b in children)
    callout = next(b for b in children if b.get("type") == "callout")
    callout_text = callout["callout"]["rich_text"][0]["text"]["content"]
    assert "Vendor Security Assessment" in callout_text
    assert "SOC2 questionnaire table" in callout_text


def test_multi_destination_routing_to_brainstorm_page(tmp_path: Path):
    """Verify BRAINSTORM classification routes to brainstorm_page_id via blocks.children.append."""
    img_path = _create_dummy_image(tmp_path / "brainstorm_sketch.png")
    item = ScreenshotItem(
        path=img_path,
        title="Q3 Roadmap Mindmap",
        classification="BRAINSTORM",
        cluster_tag="Ideation",
        rationale="Whiteboard brainstorm diagram",
    )

    mock_client = MagicMock()
    mock_client.blocks.children.append.return_value = {"results": []}

    destinations = ScreenshotDestinationsConfig(brainstorm_page_id="page_brainstorm_888")
    success = sync_screenshot_to_notion(
        item=item,
        notion_client=mock_client,
        destinations=destinations,
    )

    assert success is True
    assert item.is_synced is True
    mock_client.blocks.children.append.assert_called_once()
    call_args = mock_client.blocks.children.append.call_args[1]
    assert call_args["block_id"] == "page_brainstorm_888"
    children = call_args["children"]
    assert any(b.get("type") == "callout" for b in children)
    callout = next(b for b in children if b.get("type") == "callout")
    callout_text = callout["callout"]["rich_text"][0]["text"]["content"]
    assert "Q3 Roadmap Mindmap" in callout_text
    assert "Whiteboard brainstorm diagram" in callout_text


def test_multi_destination_preserving_local_keep_files(tmp_path: Path):
    """Verify LOCAL_KEEP classification is never synced and preserved locally."""
    img_path = _create_dummy_image(tmp_path / "random_desktop.png")
    item = ScreenshotItem(
        path=img_path,
        title="Desktop Wallpaper",
        classification="LOCAL_KEEP",
        cluster_tag="Personal",
    )

    mock_client = MagicMock()
    destinations = ScreenshotDestinationsConfig(
        work_notes_database_id="db_123",
        due_diligence_page_id="page_dd_123",
        brainstorm_page_id="page_bs_123",
    )

    synced_count, errors = sync_approved_items(
        items=[item],
        approved_cluster_keys=["Personal"],
        notion_client=mock_client,
        destinations=destinations,
    )

    assert synced_count == 0
    assert item.is_synced is False
    mock_client.pages.create.assert_not_called()
    mock_client.blocks.children.append.assert_not_called()

    deleted_count, clean_errors = cleanup_synced_files([item], delete_synced_local=True)
    assert deleted_count == 0
    assert img_path.exists()
    assert item.deleted_locally is False


def test_unconfigured_destination_safety_does_not_delete_file(tmp_path: Path):
    """CRITICAL SAFETY TEST: If destination ID for category is unconfigured, do not sync and never delete."""
    file_work = _create_dummy_image(tmp_path / "work.png")
    file_dd = _create_dummy_image(tmp_path / "dd.png")
    file_bs = _create_dummy_image(tmp_path / "bs.png")

    item_work = ScreenshotItem(path=file_work, classification="WORK_NOTES", cluster_tag="Work")
    item_dd = ScreenshotItem(path=file_dd, classification="DUE_DILIGENCE", cluster_tag="Audits")
    item_bs = ScreenshotItem(path=file_bs, classification="BRAINSTORM", cluster_tag="Ideas")

    mock_client = MagicMock()
    # Empty destinations config
    destinations = ScreenshotDestinationsConfig(
        work_notes_database_id="",
        due_diligence_page_id="",
        brainstorm_page_id="",
    )

    synced_count, errors = sync_approved_items(
        items=[item_work, item_dd, item_bs],
        approved_cluster_keys=["Work", "Audits", "Ideas"],
        notion_client=mock_client,
        destinations=destinations,
    )

    assert synced_count == 0
    assert item_work.is_synced is False
    assert item_dd.is_synced is False
    assert item_bs.is_synced is False

    assert len(errors) == 3
    assert any("Destination ID not configured for category WORK_NOTES" in e for e in errors)
    assert any("Destination ID not configured for category DUE_DILIGENCE" in e for e in errors)
    assert any("Destination ID not configured for category BRAINSTORM" in e for e in errors)

    # Attempt local cleanup
    deleted_count, clean_errors = cleanup_synced_files(
        [item_work, item_dd, item_bs],
        delete_synced_local=True,
    )
    assert deleted_count == 0
    assert file_work.exists()
    assert file_dd.exists()
    assert file_bs.exists()


def test_introspect_database_schema_via_data_sources():
    """Verify introspect_database_schema introspects properties from data_sources when properties is empty."""
    mock_client = MagicMock()
    mock_client.databases.retrieve.return_value = {
        "id": "db_test",
        "properties": None,
        "data_sources": [{"id": "ds_123", "name": "Due Diligence"}],
    }
    mock_client.data_sources.retrieve.return_value = {
        "id": "ds_123",
        "properties": {
            "Question": {"type": "title", "id": "title"},
            "Tags": {"type": "multi_select"},
            "Date Added": {"type": "date"},
        },
    }

    schema = introspect_database_schema(mock_client, "db_test")
    assert schema["title_prop"] == "Question"
    assert schema["tag_prop"] == "Tags"
    assert schema["tag_type"] == "multi_select"
    assert schema["date_prop"] == "Date Added"


def test_introspect_database_schema_maps_subject_property():
    """Verify introspect_database_schema recognizes 'Subject' as tag/category property."""
    mock_client = MagicMock()
    mock_client.databases.retrieve.return_value = {
        "id": "db_worknotes",
        "properties": {
            "Title": {"type": "title", "id": "title"},
            "Subject": {"type": "multi_select"},
            "Last Review": {"type": "date"},
        },
    }

    schema = introspect_database_schema(mock_client, "db_worknotes")
    assert schema["title_prop"] == "Title"
    assert schema["tag_prop"] == "Subject"
    assert schema["tag_type"] == "multi_select"
    assert schema["date_prop"] == "Last Review"



def test_sync_screenshot_to_notion_falls_back_to_database_when_page_append_fails(tmp_path: Path):
    """Verify when a destination was assumed to be a page but is actually a database, it automatically creates a DB page."""
    img_path = _create_dummy_image(tmp_path / "due_diligence_sheet.png")
    item = ScreenshotItem(
        path=img_path,
        title="Audit Checklist",
        classification="DUE_DILIGENCE",
        cluster_tag="Audit",
    )

    mock_client = MagicMock()
    # blocks.children.append raises Notion error indicating block doesn't support children
    mock_client.blocks.children.append.side_effect = Exception("Block does not support children.")
    mock_client.databases.retrieve.return_value = {
        "id": "page_dd_123",
        "properties": {"Question": {"type": "title", "id": "title"}},
    }
    mock_client.pages.create.return_value = {"id": "created_page_id"}

    destinations = ScreenshotDestinationsConfig(due_diligence_page_id="page_dd_123")
    success = sync_screenshot_to_notion(
        item=item,
        notion_client=mock_client,
        destinations=destinations,
    )

    assert success is True
    assert item.is_synced is True
    mock_client.blocks.children.append.assert_called_once()
    mock_client.pages.create.assert_called_once()
    payload = mock_client.pages.create.call_args[1]
    assert payload["parent"]["database_id"] == "page_dd_123"
    assert "Question" in payload["properties"]


def test_get_default_vision_llm_ollama_fallback():
    """Verify get_default_vision_llm initializes Ollama or returns None gracefully."""
    # When no keys and no ollama specified, returns None
    assert get_default_vision_llm(gemini_api_key=None, ollama_model=None, ollama_url=None) is None

    # When Ollama is specified, attempts loading ChatOllama
    with patch("langchain_ollama.ChatOllama", create=True) as mock_chat_ollama:
        llm = get_default_vision_llm(
            gemini_api_key=None,
            ollama_model="minicpm-v",
            ollama_url="http://localhost:11434",
        )
        assert llm is not None


def test_get_ollama_vision_llm_instantiation():
    """Verify get_ollama_vision_llm returns a ChatOllama instance with configured model and url."""
    from deskpilot.agent_tasks.screenshot_agent.vision import get_ollama_vision_llm

    llm = get_ollama_vision_llm(ollama_model="minicpm-v", ollama_url="http://localhost:11434")
    assert llm is not None
    assert getattr(llm, "model", None) == "minicpm-v"


def test_check_gemini_quota_empty_key():
    """Verify check_gemini_quota returns False immediately if key is empty."""
    from deskpilot.agent_tasks.screenshot_agent.vision import check_gemini_quota

    assert check_gemini_quota(gemini_api_key="") is False


def test_check_gemini_quota_success_and_exhaustion():
    """Verify check_gemini_quota returns True on successful invoke, False on 429 / quota error."""
    from deskpilot.agent_tasks.screenshot_agent.vision import check_gemini_quota

    mock_probe = MagicMock()
    mock_probe.invoke.return_value = "pong"

    with patch("langchain_google_genai.ChatGoogleGenerativeAI", return_value=mock_probe):
        assert check_gemini_quota(gemini_api_key="valid_key") is True
        mock_probe.invoke.assert_called_once_with("ping")

    # Simulate quota exhaustion
    mock_failing_probe = MagicMock()
    mock_failing_probe.invoke.side_effect = RuntimeError("ResourceExhausted: 429 Quota exceeded")

    with patch("langchain_google_genai.ChatGoogleGenerativeAI", return_value=mock_failing_probe):
        assert check_gemini_quota(gemini_api_key="valid_key") is False


def test_parse_vision_response_strict_json():
    from deskpilot.agent_tasks.screenshot_agent.vision import parse_vision_response

    raw = '{"classification": "WORK_NOTES", "title": "System Architecture", "cluster_tag": "Dev", "rationale": "High-level diagram"}'
    parsed = parse_vision_response(raw)
    assert parsed["classification"] == "WORK_NOTES"
    assert parsed["title"] == "System Architecture"
    assert parsed["cluster_tag"] == "Dev"
    assert parsed["rationale"] == "High-level diagram"


def test_parse_vision_response_markdown_and_conversational():
    from deskpilot.agent_tasks.screenshot_agent.vision import parse_vision_response

    raw = """Here is your classification:
```json
{
  "classification": "BRAINSTORM",
  "title": "Q3 Brainstorming Whiteboard",
  "cluster_tag": "Ideas",
  "rationale": "Sticky notes and diagrams"
}
```
Hope that helps!"""
    parsed = parse_vision_response(raw)
    assert parsed["classification"] == "BRAINSTORM"
    assert parsed["title"] == "Q3 Brainstorming Whiteboard"
    assert parsed["cluster_tag"] == "Ideas"


def test_parse_vision_response_regex_fuzzy_extraction_on_broken_json():
    from deskpilot.agent_tasks.screenshot_agent.vision import parse_vision_response

    # Malformed JSON (missing closing braces, unescaped quote in rationale)
    raw = '{"classification": "DUE_DILIGENCE", "title": "SOC2 Questionnaire", "cluster_tag": "Compliance", "rationale": "Vendor said "approved" here'
    parsed = parse_vision_response(raw)
    assert parsed["classification"] == "DUE_DILIGENCE"
    assert parsed["title"] == "SOC2 Questionnaire"
    assert parsed["cluster_tag"] == "Compliance"


def test_parse_vision_response_heuristic_fallback():
    from deskpilot.agent_tasks.screenshot_agent.vision import parse_vision_response

    raw = "Based on the image, this is clearly a WORK_NOTES screenshot showing terminal commands."
    parsed = parse_vision_response(raw)
    assert parsed["classification"] == "WORK_NOTES"


def test_sync_approved_items_interactive_token_recovery(tmp_path: Path):
    """Verify 401 unauthorized token error prompts user for new token and retries sync."""
    from deskpilot.agent_tasks.screenshot_agent.notion_sync import sync_approved_items

    img_path = _create_dummy_image(tmp_path / "a.png")
    item = ScreenshotItem(path=img_path, classification="WORK_NOTES", cluster_tag="Dev")
    mock_client = MagicMock()
    # First call raises 401 unauthorized, second succeeds
    mock_client.pages.create.side_effect = [Exception("unauthorized 401 invalid token"), {"id": "page_ok"}]
    mock_client.databases.retrieve.return_value = {"id": "db_1", "properties": {"Title": {"type": "title"}}}

    prompts = []

    def fake_prompt(msg: str) -> str:
        prompts.append(msg)
        return "secret_new_valid_token"

    synced, errors = sync_approved_items(
        items=[item],
        approved_cluster_keys=["Dev"],
        notion_client=mock_client,
        database_id="db_1",
        prompt_func=fake_prompt,
    )
    assert len(prompts) == 1
    assert "token" in prompts[0].lower()


def test_sync_approved_items_interactive_db_id_recovery(tmp_path: Path):
    """Verify 404 object_not_found database error prompts user for new DB ID and retries."""
    from deskpilot.agent_tasks.screenshot_agent.notion_sync import sync_approved_items

    img_path = _create_dummy_image(tmp_path / "b.png")
    item = ScreenshotItem(path=img_path, classification="WORK_NOTES", cluster_tag="Dev")
    mock_client = MagicMock()
    # 404 on bad database
    mock_client.databases.retrieve.side_effect = [
        Exception("object_not_found 404"),
        {"id": "db_corrected", "properties": {"Title": {"type": "title"}}},
    ]
    mock_client.pages.create.return_value = {"id": "page_ok"}

    prompts = []

    def fake_prompt(msg: str) -> str:
        prompts.append(msg)
        return "db_corrected"

    synced, errors = sync_approved_items(
        items=[item],
        approved_cluster_keys=["Dev"],
        notion_client=mock_client,
        database_id="db_bad",
        prompt_func=fake_prompt,
    )
    assert len(prompts) == 1
    assert any(term in prompts[0].lower() for term in ("database", "id", "key", "target"))


def test_sync_approved_items_empty_prompt_finishes_without_notion(tmp_path: Path):
    """Verify pressing Enter (empty prompt) skips Notion sync gracefully without failing local triage."""
    from deskpilot.agent_tasks.screenshot_agent.notion_sync import sync_approved_items

    img_path = _create_dummy_image(tmp_path / "c.png")
    item = ScreenshotItem(path=img_path, classification="WORK_NOTES", cluster_tag="Dev")
    mock_client = MagicMock()
    mock_client.databases.retrieve.side_effect = Exception("object_not_found 404")

    synced, errors = sync_approved_items(
        items=[item],
        approved_cluster_keys=["Dev"],
        notion_client=mock_client,
        database_id="db_bad",
        prompt_func=lambda _: "",  # User pressed Enter with empty key
    )
    assert synced == 0
    assert item.is_synced is False
    assert any(
        "without passing to notion" in err.lower() or "skipped" in err.lower() or "not found" in err.lower()
        for err in errors
    )


def test_upload_screenshot_to_notion_success(tmp_path: Path):
    """Verify upload_screenshot_to_notion creates and sends file via notion_client."""
    from deskpilot.agent_tasks.screenshot_agent.notion_sync import upload_screenshot_to_notion

    img_file = tmp_path / "test.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\nfakecontent")

    mock_client = MagicMock()
    mock_client.file_uploads.create.return_value = {"id": "fu_123"}
    mock_client.file_uploads.send.return_value = {"id": "fu_123", "status": "uploaded"}

    file_id = upload_screenshot_to_notion(mock_client, img_file)
    assert file_id == "fu_123"
    mock_client.file_uploads.create.assert_called_once_with(filename="test.png", content_type="image/png")
    mock_client.file_uploads.send.assert_called_once()


def test_upload_screenshot_to_notion_jpeg_success(tmp_path: Path):
    """Verify upload_screenshot_to_notion correctly detects JPEG mime type."""
    from deskpilot.agent_tasks.screenshot_agent.notion_sync import upload_screenshot_to_notion

    img_file = tmp_path / "test.jpeg"
    img_file.write_bytes(b"\xff\xd8\xfffakecontent")

    mock_client = MagicMock()
    mock_client.file_uploads.create.return_value = {"id": "fu_jpeg_456"}
    mock_client.file_uploads.send.return_value = {"id": "fu_jpeg_456"}

    file_id = upload_screenshot_to_notion(mock_client, img_file)
    assert file_id == "fu_jpeg_456"
    mock_client.file_uploads.create.assert_called_once_with(filename="test.jpeg", content_type="image/jpeg")


def test_upload_screenshot_to_notion_error_returns_none(tmp_path: Path):
    """Verify upload_screenshot_to_notion returns None on missing file or API error without raising."""
    from deskpilot.agent_tasks.screenshot_agent.notion_sync import upload_screenshot_to_notion

    # Case 1: Missing file
    img_file = tmp_path / "missing.png"
    mock_client = MagicMock()
    file_id = upload_screenshot_to_notion(mock_client, img_file)
    assert file_id is None

    # Case 2: API error during upload
    real_file = tmp_path / "exists.png"
    real_file.write_bytes(b"data")
    mock_client.file_uploads.create.side_effect = RuntimeError("Notion file upload failed")
    file_id = upload_screenshot_to_notion(mock_client, real_file)
    assert file_id is None


def test_build_database_page_payload_with_image(tmp_path: Path):
    """Verify image block is included in children blocks below details paragraph."""
    from deskpilot.agent_tasks.screenshot_agent.notion_sync import build_database_page_payload

    item = ScreenshotItem(path=tmp_path / "shot.png", title="Test Title", cluster_tag="Work")
    payload = build_database_page_payload(item, database_id="db_1", schema={}, file_upload_id="fu_abc")

    children = payload.get("children", [])
    assert len(children) == 3
    assert children[0]["type"] == "heading_2"
    assert children[1]["type"] == "paragraph"
    para_content = children[1]["paragraph"]["rich_text"][0]["text"]["content"]
    assert "Rationale:" in para_content
    assert "Category:" not in para_content
    assert "Source Screenshot:" not in para_content
    assert children[2]["type"] == "image"
    assert children[2]["image"]["file_upload"]["id"] == "fu_abc"


def test_build_page_append_blocks_with_image(tmp_path: Path):
    """Verify image block is appended to blocks when file_upload_id is provided."""
    from deskpilot.agent_tasks.screenshot_agent.notion_sync import build_page_append_blocks

    item = ScreenshotItem(path=tmp_path / "shot.png", title="Test Title", cluster_tag="Work")
    blocks = build_page_append_blocks(item, file_upload_id="fu_xyz")

    assert len(blocks) == 2
    assert blocks[0]["type"] == "callout"
    callout_text = blocks[0]["callout"]["rich_text"][0]["text"]["content"]
    assert "Rationale:" in callout_text
    assert "Category:" not in callout_text
    assert "Source:" not in callout_text
    assert blocks[1]["type"] == "image"
    assert blocks[1]["image"]["file_upload"]["id"] == "fu_xyz"


def test_match_existing_page_exact_and_token_overlap(tmp_path: Path):
    """Verify token overlap finds existing page without calling LLM."""
    from deskpilot.agent_tasks.screenshot_agent.notion_sync import match_existing_page

    pages = [
        {"id": "p1", "title": "Microsoft Copilot Studio Notes"},
        {"id": "p2", "title": "Job Prospect of an AI Engineer"},
    ]

    item1 = ScreenshotItem(path=tmp_path / "a.png", title="Job Prospect of an AI Engineer")
    assert match_existing_page(item1, pages) == "p2"

    item2 = ScreenshotItem(path=tmp_path / "b.png", title="AI Engineer Job Prospects")
    assert match_existing_page(item2, pages) == "p2"

    item3 = ScreenshotItem(path=tmp_path / "c.png", title="Unrelated Cooking Recipe")
    assert match_existing_page(item3, pages) is None


def test_match_existing_page_llm_verification(tmp_path: Path):
    """Verify LLM is queried when token overlap is ambiguous."""
    from deskpilot.agent_tasks.screenshot_agent.notion_sync import match_existing_page

    pages = [{"id": "p1", "title": "AI Engineering Careers & Outlook"}]
    item = ScreenshotItem(path=tmp_path / "d.png", title="Tech Salary 2026", rationale="AI engineer comp")

    mock_llm = MagicMock()
    mock_llm.invoke.return_value.content = "AI Engineering Careers & Outlook"

    assert match_existing_page(item, pages, llm=mock_llm) == "p1"

    # LLM says NONE
    mock_llm.invoke.return_value.content = "NONE"
    assert match_existing_page(item, pages, llm=mock_llm) is None


def test_build_page_append_section_blocks(tmp_path: Path):
    """Verify build_page_append_section_blocks builds divider, heading_3, details paragraph and optional image block."""
    from deskpilot.agent_tasks.screenshot_agent.notion_sync import build_page_append_section_blocks

    item = ScreenshotItem(path=tmp_path / "shot.png", title="AI Salaries", cluster_tag="Work", rationale="High pay")
    blocks_no_img = build_page_append_section_blocks(item)

    assert len(blocks_no_img) == 3
    assert blocks_no_img[0]["type"] == "divider"
    assert blocks_no_img[1]["type"] == "heading_3"
    assert blocks_no_img[1]["heading_3"]["rich_text"][0]["text"]["content"] == "AI Salaries"
    assert blocks_no_img[2]["type"] == "paragraph"
    content_text = blocks_no_img[2]["paragraph"]["rich_text"][0]["text"]["content"]
    assert "Rationale: High pay" in content_text
    assert "Category:" not in content_text
    assert "Source Screenshot:" not in content_text

    # With image
    blocks_with_img = build_page_append_section_blocks(item, file_upload_id="fu_789")
    assert len(blocks_with_img) == 4
    assert blocks_with_img[3]["type"] == "image"
    assert blocks_with_img[3]["image"]["file_upload"]["id"] == "fu_789"


def test_fetch_existing_database_pages():
    """Verify fetch_existing_database_pages queries data sources or search and extracts plain title."""
    from deskpilot.agent_tasks.screenshot_agent.notion_sync import fetch_existing_database_pages

    mock_client = MagicMock()

    # Case 1: database has data_sources, query client.data_sources
    mock_client.databases.retrieve.return_value = {
        "id": "db_123",
        "data_sources": [{"id": "ds_456"}],
    }
    mock_client.data_sources.query.return_value = {
        "results": [
            {
                "id": "page_1",
                "properties": {
                    "Name": {
                        "type": "title",
                        "title": [{"plain_text": "Existing Page 1"}],
                    }
                },
            }
        ]
    }

    res = fetch_existing_database_pages(mock_client, "db_123")
    assert res == [{"id": "page_1", "title": "Existing Page 1"}]

    # Case 2: search fallback
    delattr(mock_client, "data_sources")
    mock_client.databases.retrieve.return_value = {"id": "db_123", "data_sources": []}
    mock_client.search.return_value = {
        "results": [
            {
                "id": "page_2",
                "parent": {"database_id": "db_123"},
                "properties": {
                    "Title": {
                        "type": "title",
                        "title": [{"plain_text": "Search Found Page"}],
                    }
                },
            },
            {
                "id": "page_other",
                "parent": {"database_id": "other_db"},
                "properties": {
                    "Title": {
                        "type": "title",
                        "title": [{"plain_text": "Other Page"}],
                    }
                },
            },
        ]
    }
    res2 = fetch_existing_database_pages(mock_client, "db_123")
    assert res2 == [{"id": "page_2", "title": "Search Found Page"}]

    # Case 3: Error returns empty list
    mock_client.databases.retrieve.side_effect = RuntimeError("Notion error")
    assert fetch_existing_database_pages(mock_client, "db_123") == []


def test_sync_screenshot_to_notion_consolidates_into_existing_page(tmp_path: Path):
    """Verify that when an existing page matches, sync appends section blocks instead of creating a new page."""
    img_file = _create_dummy_image(tmp_path / "copilot_notes.png")
    item = ScreenshotItem(
        path=img_file,
        filename="copilot_notes.png",
        title="Copilot Studio Architecture",
        classification="WORK_NOTES",
        cluster_tag="Engineering",
        rationale="Detailed diagram",
    )

    mock_client = MagicMock()
    mock_client.file_uploads.create.return_value = {"id": "fu_uploaded_1"}
    mock_client.file_uploads.send.return_value = {"id": "fu_uploaded_1", "status": "uploaded"}

    page_cache: dict[str, list[dict[str, str]]] = {
        "db_123": [{"id": "page_copilot_999", "title": "Copilot Studio Architecture Overview"}]
    }

    success = sync_screenshot_to_notion(
        item=item,
        notion_client=mock_client,
        database_id="db_123",
        page_cache=page_cache,
        consolidate_pages=True,
    )

    assert success is True
    assert item.is_synced is True
    # Verify blocks.children.append was called on the matched page ID
    mock_client.blocks.children.append.assert_called_once()
    call_kwargs = mock_client.blocks.children.append.call_args[1]
    assert call_kwargs["block_id"] == "page_copilot_999"
    children = call_kwargs["children"]
    assert any(b.get("type") == "image" for b in children)
    # Ensure pages.create was NOT called
    mock_client.pages.create.assert_not_called()


def test_sync_screenshot_to_notion_creates_new_page_with_image_when_no_match(tmp_path: Path):
    """Verify that when no existing page matches, sync creates a new page with image block and updates page_cache."""
    img_file = _create_dummy_image(tmp_path / "new_topic.png")
    item = ScreenshotItem(
        path=img_file,
        filename="new_topic.png",
        title="Brand New Unrelated Topic",
        classification="WORK_NOTES",
        cluster_tag="Research",
        rationale="New finding",
    )

    mock_client = MagicMock()
    mock_client.file_uploads.create.return_value = {"id": "fu_uploaded_2"}
    mock_client.file_uploads.send.return_value = {"id": "fu_uploaded_2", "status": "uploaded"}
    mock_client.databases.retrieve.return_value = {"id": "db_123", "properties": {"Name": {"type": "title"}}}
    mock_client.pages.create.return_value = {"id": "page_new_555"}

    page_cache: dict[str, list[dict[str, str]]] = {
        "db_123": [{"id": "page_copilot_999", "title": "Copilot Studio Architecture Overview"}]
    }

    success = sync_screenshot_to_notion(
        item=item,
        notion_client=mock_client,
        database_id="db_123",
        page_cache=page_cache,
        consolidate_pages=True,
    )

    assert success is True
    assert item.is_synced is True
    # Verify pages.create was called with image in payload
    mock_client.pages.create.assert_called_once()
    create_kwargs = mock_client.pages.create.call_args[1]
    children = create_kwargs.get("children", [])
    assert any(b.get("type") == "image" for b in children)
    # Verify page_cache was updated with newly created page
    assert {"id": "page_new_555", "title": "Brand New Unrelated Topic"} in page_cache["db_123"]
    # Ensure blocks.children.append was not called
    mock_client.blocks.children.append.assert_not_called()


def test_introspect_database_schema_extracts_tag_options():
    """Verify introspect_database_schema extracts list of existing tag options."""
    mock_client = MagicMock()
    mock_client.databases.retrieve.return_value = {
        "id": "db_1",
        "properties": {
            "Title": {"type": "title", "id": "title"},
            "Subject": {
                "type": "multi_select",
                "multi_select": {
                    "options": [
                        {"name": "Python & AI Engineering"},
                        {"name": "Backend/Database"},
                        {"name": "CyberSec"},
                    ]
                },
            },
        },
    }

    schema = introspect_database_schema(mock_client, "db_1")
    assert schema["tag_prop"] == "Subject"
    assert schema["tag_options"] == ["Python & AI Engineering", "Backend/Database", "CyberSec"]


def test_classify_screenshot_uses_available_tags_and_snaps(tmp_path: Path):
    """Verify classify_screenshot prompts with available tags and snaps fuzzy match."""
    img = _create_dummy_image(tmp_path / "code.png")

    item = ScreenshotItem(path=img, filename="code.png")
    mock_llm = MagicMock()
    mock_llm.invoke.return_value.content = (
        '{"classification": "WORK_NOTES", "title": "PyTorch Guide", "cluster_tag": "python", "rationale": "Deep learning"}'
    )

    available = ["Python & AI Engineering", "CyberSec", "WEBDEV"]
    updated = classify_screenshot(item, llm=mock_llm, available_tags=available)

    assert updated.cluster_tag == "Python & AI Engineering"


@pytest.mark.asyncio
async def test_run_screenshot_triage_introspects_and_passes_tag_options(tmp_path: Path):
    """Verify run_screenshot_triage introspects database schema for tag_options and vision_triage uses them."""
    shot = _create_dummy_image(tmp_path / "firewall.png")

    settings = Settings(
        _env_file=None,
        screenshots=ScreenshotsConfig(directory=tmp_path, delete_synced_local=False),
        notion=NotionConfig(
            token="mock_token",
            screenshot_destinations=ScreenshotDestinationsConfig(
                work_notes_database_id="db_work_sec",
            ),
        ),
    )

    mock_client = MagicMock()
    mock_client.pages.create.return_value = {"id": "page_sec_1"}

    with patch(
        "deskpilot.agent_tasks.screenshot_agent.graph.introspect_database_schema"
    ) as mock_introspect, patch(
        "deskpilot.agent_tasks.screenshot_agent.graph.triage_screenshots"
    ) as mock_triage:
        mock_introspect.return_value = {
            "title_prop": "Topic",
            "tag_prop": "Subject",
            "tag_options": ["CyberSec", "Design"],
        }
        # mock triage_screenshots to return the items unchanged and no errors
        mock_triage.side_effect = lambda items, llm=None, available_tags=None: (items, [])

        mock_llm = MagicMock()

        result_state = await run_screenshot_triage(
            settings=settings,
            auto_approve=True,
            llm=mock_llm,
            notion_client=mock_client,
        )

        mock_introspect.assert_called_once_with(mock_client, "db_work_sec")
        assert result_state.get("tag_options") == ["CyberSec", "Design"]
        mock_triage.assert_called_once()
        _, kwargs = mock_triage.call_args
        assert kwargs.get("available_tags") == ["CyberSec", "Design"]




