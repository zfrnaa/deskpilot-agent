"""Tests for Screenshot Agent cross-thread memory (BaseStore) and selective serialization."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image
from langgraph.checkpoint.sqlite import SqliteSaver

from deskpilot.agent_tasks.screenshot_agent.graph import (
    build_screenshot_triage_graph,
    notion_sync,
    run_screenshot_triage,
    vision_triage,
)
from deskpilot.agent_tasks.screenshot_agent.state import ScreenshotAgentState, ScreenshotItem
from deskpilot.agent_tasks.screenshot_agent.vision import (
    classify_screenshot,
    format_preferences,
    triage_screenshots,
)
from deskpilot.config import NotionConfig, ScreenshotDestinationsConfig, ScreenshotsConfig, Settings
from deskpilot.storage.store import SQLiteStore, get_store


def _create_dummy_image(path: Path, color: str = "blue", size: tuple[int, int] = (100, 100)) -> Path:
    """Helper to create a valid dummy image file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", size, color=color)
    img.save(path)
    return path


# ---------------------------------------------------------------------------
# Cross-Thread Memory Tests
# ---------------------------------------------------------------------------


def test_format_preferences_formats_dict_and_list() -> None:
    """Verify format_preferences formats both dict and list routing memories."""
    dict_pref = {
        "Invoices": {"classification": "WORK_NOTES", "cluster_tag": "Invoices"},
        "Gaming": {"classification": "LOCAL_KEEP", "cluster_tag": "Gaming"},
    }
    formatted = format_preferences(dict_pref)
    assert "- 'Invoices': classify as WORK_NOTES (cluster tag: 'Invoices')" in formatted
    assert "- 'Gaming': classify as LOCAL_KEEP (cluster tag: 'Gaming')" in formatted

    list_pref = [
        {"key": "Taxes", "classification": "WORK_NOTES", "cluster_tag": "Taxes"},
    ]
    formatted_list = format_preferences(list_pref)
    assert "- 'Taxes': classify as WORK_NOTES (cluster tag: 'Taxes')" in formatted_list
    assert format_preferences(None) == ""
    assert format_preferences({}) == ""


def test_classify_screenshot_includes_learned_preferences_in_prompt(tmp_path: Path) -> None:
    """Verify classify_screenshot incorporates learned preferences into the LLM prompt."""
    img_path = _create_dummy_image(tmp_path / "invoice.png")
    item = ScreenshotItem(path=img_path, filename="invoice.png")

    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = (
        '{"classification": "WORK_NOTES", "title": "Quarterly Invoice", "cluster_tag": "Finance", "rationale": "Invoice"}'
    )
    mock_llm.invoke.return_value = mock_response

    learned_prefs = {
        "Finance": {"classification": "WORK_NOTES", "cluster_tag": "Finance"},
    }

    result_item = classify_screenshot(
        item=item,
        llm=mock_llm,
        learned_preferences=learned_prefs,
    )

    assert result_item.classification == "WORK_NOTES"
    assert result_item.cluster_tag == "Finance"
    mock_llm.invoke.assert_called_once()
    call_args = mock_llm.invoke.call_args[0][0]
    # Check human message content text for learned preferences
    human_msg = call_args[0]
    prompt_text = human_msg.content[0]["text"]
    assert "Learned User Preferences from past sessions:" in prompt_text
    assert "- 'Finance': classify as WORK_NOTES" in prompt_text


def test_vision_triage_recalls_preferences_from_sqlite_store(tmp_path: Path) -> None:
    """Verify vision_triage queries ('screenshots', 'routing_preferences') from SQLiteStore."""
    store_file = tmp_path / "test_store.db"
    store = SQLiteStore(store_file)
    store.put(
        ("screenshots", "routing_preferences"),
        key="Receipts",
        value={"classification": "WORK_NOTES", "cluster_tag": "Receipts"},
    )
    store.put(
        ("screenshots", "routing_preferences"),
        key="Memes",
        value={"classification": "LOCAL_KEEP", "cluster_tag": "Memes"},
    )

    img_file = _create_dummy_image(tmp_path / "doc.png")
    item = ScreenshotItem(path=img_file, filename="doc.png")

    state: ScreenshotAgentState = {
        "screenshots_dir": tmp_path,
        "max_images": 10,
        "items": [item],
        "clusters": {},
        "approved_cluster_keys": [],
        "synced_count": 0,
        "deleted_count": 0,
        "errors": [],
    }

    captured_prefs: dict | None = None

    def mock_llm_callable(i: ScreenshotItem, learned_preferences: dict | None = None) -> ScreenshotItem:
        nonlocal captured_prefs
        captured_prefs = learned_preferences
        i.classification = "WORK_NOTES"
        i.cluster_tag = "Receipts"
        return i

    res = vision_triage(state, llm=mock_llm_callable, store=store)

    assert len(res["items"]) == 1
    assert captured_prefs is not None
    assert "Receipts" in captured_prefs
    assert "Memes" in captured_prefs
    assert captured_prefs["Receipts"]["classification"] == "WORK_NOTES"


def test_notion_sync_persists_approved_preferences_to_store(tmp_path: Path) -> None:
    """Verify notion_sync stores routing preferences into SQLiteStore for approved items."""
    store_file = tmp_path / "sync_store.db"
    store = SQLiteStore(store_file)

    img1 = _create_dummy_image(tmp_path / "tax.png")
    img2 = _create_dummy_image(tmp_path / "game.png")

    item1 = ScreenshotItem(
        path=img1,
        filename="tax.png",
        classification="WORK_NOTES",
        cluster_tag="Taxes",
    )
    item2 = ScreenshotItem(
        path=img2,
        filename="game.png",
        classification="LOCAL_KEEP",
        cluster_tag="Gaming",
    )

    state: ScreenshotAgentState = {
        "screenshots_dir": tmp_path,
        "max_images": 10,
        "items": [item1, item2],
        "clusters": {"Taxes": [item1], "Gaming": [item2]},
        "approved_cluster_keys": ["Taxes"],
        "synced_count": 0,
        "deleted_count": 0,
        "errors": [],
    }

    mock_client = MagicMock()
    mock_client.pages.create.return_value = {"id": "page_tax_1"}

    with patch(
        "deskpilot.agent_tasks.screenshot_agent.graph.sync_approved_items",
        return_value=(1, []),
    ):
        result = notion_sync(state, notion_client=mock_client, store=store)

    assert result["synced_count"] == 1
    # Verify item1 was stored in SQLiteStore under ('screenshots', 'routing_preferences')
    tax_pref = store.get(("screenshots", "routing_preferences"), "Taxes")
    assert tax_pref is not None
    assert tax_pref.value["classification"] == "WORK_NOTES"
    assert tax_pref.value["cluster_tag"] == "Taxes"

    # Gaming was not approved, so it should not be stored
    game_pref = store.get(("screenshots", "routing_preferences"), "Gaming")
    assert game_pref is None


@pytest.mark.asyncio
async def test_end_to_end_cross_thread_memory_learning(tmp_path: Path) -> None:
    """End-to-end test: run graph session 1 to learn preferences, session 2 recalls them."""
    store_file = tmp_path / "e2e_store.db"
    store = SQLiteStore(store_file)

    # Session 1: process image and approve
    img1 = _create_dummy_image(tmp_path / "img1.png")

    def mock_vision_session_1(item: ScreenshotItem, learned_preferences: dict | None = None) -> ScreenshotItem:
        item.classification = "WORK_NOTES"
        item.cluster_tag = "Architecture"
        item.title = "System Diagram"
        return item

    mock_notion = MagicMock()
    mock_notion.pages.create.return_value = {"id": "page_1"}

    graph1 = build_screenshot_triage_graph(
        llm=mock_vision_session_1,
        notion_client=mock_notion,
        database_id="db_work_test",
        store=store,
        auto_approve=True,
        delete_synced_local=False,
    )

    state1: ScreenshotAgentState = {
        "screenshots_dir": tmp_path,
        "max_images": 1,
        "items": [],
        "clusters": {},
        "approved_cluster_keys": [],
        "synced_count": 0,
        "deleted_count": 0,
        "errors": [],
        "auto_approve": True,
        "delete_synced_local": False,
        "database_id": "db_work_test",
    }

    res1 = await graph1.ainvoke(state1)
    assert res1["synced_count"] == 1

    # Verify store has learned Architecture
    saved_pref = store.get(("screenshots", "routing_preferences"), "Architecture")
    assert saved_pref is not None
    assert saved_pref.value["classification"] == "WORK_NOTES"

    # Session 2: run with mock vision that checks received learned preferences
    session_2_recalled_prefs = None

    def mock_vision_session_2(item: ScreenshotItem, learned_preferences: dict | None = None) -> ScreenshotItem:
        nonlocal session_2_recalled_prefs
        session_2_recalled_prefs = learned_preferences
        item.classification = "WORK_NOTES"
        item.cluster_tag = "Architecture"
        return item

    graph2 = build_screenshot_triage_graph(
        llm=mock_vision_session_2,
        notion_client=mock_notion,
        database_id="db_work_test",
        store=store,
        auto_approve=True,
        delete_synced_local=False,
    )

    state2: ScreenshotAgentState = {
        "screenshots_dir": tmp_path,
        "max_images": 1,
        "items": [],
        "clusters": {},
        "approved_cluster_keys": [],
        "synced_count": 0,
        "deleted_count": 0,
        "errors": [],
        "auto_approve": True,
        "delete_synced_local": False,
        "database_id": "db_work_test",
    }

    await graph2.ainvoke(state2)
    assert session_2_recalled_prefs is not None
    assert "Architecture" in session_2_recalled_prefs
    assert session_2_recalled_prefs["Architecture"]["classification"] == "WORK_NOTES"


# ---------------------------------------------------------------------------
# Selective State Serialization Tests
# ---------------------------------------------------------------------------


def test_selective_state_serialization_no_base64_in_checkpoints(tmp_path: Path) -> None:
    """Verify checkpoint data in SqliteSaver contains ZERO base64 image strings (>10,000 chars)."""
    img_file = _create_dummy_image(tmp_path / "large_photo.png", size=(400, 400), color="red")
    cp_file = tmp_path / "checkpoints.db"

    conn = sqlite3.connect(str(cp_file), check_same_thread=False)
    cp = SqliteSaver(conn)
    cp.setup()

    def mock_llm_callable(item: ScreenshotItem) -> ScreenshotItem:
        item.classification = "WORK_NOTES"
        item.cluster_tag = "Designs"
        item.title = "Red Canvas"
        return item

    graph = build_screenshot_triage_graph(
        llm=mock_llm_callable,
        checkpointer=cp,
        auto_approve=False,
        delete_synced_local=False,
    )

    state: ScreenshotAgentState = {
        "screenshots_dir": tmp_path,
        "max_images": 1,
        "items": [],
        "clusters": {},
        "approved_cluster_keys": [],
        "synced_count": 0,
        "deleted_count": 0,
        "errors": [],
        "auto_approve": False,
        "delete_synced_local": False,
    }

    graph.invoke(state, config={"configurable": {"thread_id": "thread-ser-test"}})

    # Read all raw checkpoint records from SQLite
    cur = conn.cursor()
    rows = cur.execute("SELECT checkpoint FROM checkpoints WHERE thread_id = 'thread-ser-test'").fetchall()
    conn.close()

    assert len(rows) > 0, "Checkpoints should be recorded"

    for r in rows:
        raw_checkpoint = str(r[0])
        # Base64 representations of images are typically > 10,000 characters
        # Verify no string or value in the checkpoint exceeds 10,000 characters
        assert len(raw_checkpoint) < 10000, f"Checkpoint size unexpectedly large ({len(raw_checkpoint)} chars)"
        assert "data:image/" not in raw_checkpoint
        assert ";base64," not in raw_checkpoint


@pytest.mark.asyncio
async def test_run_screenshot_triage_accepts_checkpointer_and_store(tmp_path: Path) -> None:
    """Verify run_screenshot_triage accepts and utilizes custom checkpointer and store."""
    img_file = _create_dummy_image(tmp_path / "report.png")
    store_file = tmp_path / "custom_store.db"
    store = SQLiteStore(store_file)

    settings = Settings(
        _env_file=None,
        screenshots=ScreenshotsConfig(directory=tmp_path, delete_synced_local=False),
        notion=NotionConfig(
            token="mock_token",
            screenshot_destinations=ScreenshotDestinationsConfig(
                work_notes_database_id="db_custom_test",
            ),
        ),
    )

    mock_client = MagicMock()
    mock_client.pages.create.return_value = {"id": "page_test_1"}

    def mock_llm_fn(item: ScreenshotItem, learned_preferences: dict | None = None) -> ScreenshotItem:
        item.classification = "WORK_NOTES"
        item.cluster_tag = "Reports"
        item.title = "Annual Report"
        return item

    result_state = await run_screenshot_triage(
        settings=settings,
        auto_approve=True,
        llm=mock_llm_fn,
        notion_client=mock_client,
        store=store,
    )

    assert result_state["synced_count"] == 1
    # Verify store has captured the Reports preference
    pref = store.get(("screenshots", "routing_preferences"), "Reports")
    assert pref is not None
    assert pref.value["classification"] == "WORK_NOTES"
