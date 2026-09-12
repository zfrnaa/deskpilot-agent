"""Unit tests for ReAct consolidation agent integration into notion_sync and graph."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from deskpilot.agent_tasks.screenshot_agent.graph import (
    build_screenshot_triage_graph,
    notion_sync,
    run_screenshot_triage,
)
from deskpilot.agent_tasks.screenshot_agent.notion_sync import (
    sync_approved_items,
    sync_screenshot_to_notion,
)
from deskpilot.agent_tasks.screenshot_agent.state import ScreenshotAgentState, ScreenshotItem
from deskpilot.config import NotionConfig, ScreenshotDestinationsConfig, ScreenshotsConfig, Settings


def _create_dummy_image(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (50, 50), color="green")
    img.save(path)
    return path


def test_sync_screenshot_to_notion_invokes_react_consolidation_agent_when_reasoning_llm_provided(tmp_path: Path):
    """Verify that sync_screenshot_to_notion invokes run_react_consolidation_agent when reasoning_llm is passed."""
    img_file = _create_dummy_image(tmp_path / "shot.png")
    item = ScreenshotItem(
        path=img_file,
        filename="shot.png",
        title="Architecture Diagram",
        classification="WORK_NOTES",
        cluster_tag="Engineering",
        rationale="System diagram",
    )

    mock_client = MagicMock()
    mock_reasoning_llm = MagicMock()
    mock_fallback_llm = MagicMock()

    with (
        patch("deskpilot.agent_tasks.screenshot_agent.notion_sync.upload_screenshot_to_notion", return_value="upload-999"),
        patch("deskpilot.agent_tasks.screenshot_agent.notion_sync.introspect_database_schema", return_value={"title_prop": "Name"}),
        patch("deskpilot.agent_tasks.screenshot_agent.react_agent.run_react_consolidation_agent") as mock_react,
    ):
        mock_react.return_value = {
            "action": "append",
            "page_id": "page-123",
            "synced": True,
            "error": None,
        }

        success = sync_screenshot_to_notion(
            item=item,
            notion_client=mock_client,
            database_id="test-db",
            reasoning_llm=mock_reasoning_llm,
            fallback_reasoning_llm=mock_fallback_llm,
        )

        assert success is True
        assert item.is_synced is True
        mock_react.assert_called_once_with(
            item=item,
            notion_client=mock_client,
            database_id="test-db",
            schema={"title_prop": "Name"},
            llm=mock_reasoning_llm,
            fallback_llm=mock_fallback_llm,
            file_upload_id="upload-999",
        )


def test_sync_screenshot_to_notion_falls_back_to_match_existing_page_when_no_reasoning_llm(tmp_path: Path):
    """Verify backward compatibility: if reasoning_llm is None, uses match_existing_page."""
    img_file = _create_dummy_image(tmp_path / "shot.png")
    item = ScreenshotItem(
        path=img_file,
        filename="shot.png",
        title="Copilot Architecture",
        classification="WORK_NOTES",
        cluster_tag="Engineering",
    )

    mock_client = MagicMock()
    with (
        patch("deskpilot.agent_tasks.screenshot_agent.notion_sync.upload_screenshot_to_notion", return_value="upload-999"),
        patch("deskpilot.agent_tasks.screenshot_agent.notion_sync.fetch_existing_database_pages", return_value=[{"id": "p1", "title": "Copilot Architecture"}]),
        patch("deskpilot.agent_tasks.screenshot_agent.notion_sync.match_existing_page", return_value="p1") as mock_match,
        patch("deskpilot.agent_tasks.screenshot_agent.react_agent.run_react_consolidation_agent") as mock_react,
    ):
        mock_client.blocks.children.append.return_value = {"results": []}

        success = sync_screenshot_to_notion(
            item=item,
            notion_client=mock_client,
            database_id="test-db",
            reasoning_llm=None,
        )

        assert success is True
        assert item.is_synced is True
        mock_match.assert_called_once()
        mock_react.assert_not_called()


def test_sync_approved_items_passes_reasoning_llms_to_sync_screenshot_to_notion(tmp_path: Path):
    """Verify sync_approved_items passes reasoning_llm and fallback_reasoning_llm to sync_screenshot_to_notion."""
    img_file = _create_dummy_image(tmp_path / "shot.png")
    item = ScreenshotItem(
        path=img_file,
        filename="shot.png",
        title="Notes",
        classification="WORK_NOTES",
        cluster_tag="Dev",
    )

    mock_client = MagicMock()
    mock_reasoning_llm = MagicMock()
    mock_fallback_llm = MagicMock()

    with patch("deskpilot.agent_tasks.screenshot_agent.notion_sync.sync_screenshot_to_notion", return_value=True) as mock_sync:
        synced_count, errors = sync_approved_items(
            items=[item],
            approved_cluster_keys=["Dev"],
            notion_client=mock_client,
            database_id="test-db",
            reasoning_llm=mock_reasoning_llm,
            fallback_reasoning_llm=mock_fallback_llm,
        )

        assert synced_count == 1
        assert not errors
        mock_sync.assert_called_once()
        kwargs = mock_sync.call_args[1]
        assert kwargs.get("reasoning_llm") == mock_reasoning_llm
        assert kwargs.get("fallback_reasoning_llm") == mock_fallback_llm


def test_notion_sync_node_wires_reasoning_llms_from_state_or_args(tmp_path: Path):
    """Verify notion_sync node extracts reasoning_llm and fallback_reasoning_llm and passes them down."""
    item = ScreenshotItem(path=tmp_path / "shot.png", classification="WORK_NOTES", cluster_tag="Dev")
    mock_reasoning_llm = MagicMock()
    mock_fallback_llm = MagicMock()

    state: ScreenshotAgentState = {
        "items": [item],
        "approved_cluster_keys": ["Dev"],
        "database_id": "test-db",
        "reasoning_llm": mock_reasoning_llm,
        "fallback_reasoning_llm": mock_fallback_llm,
    }

    with patch("deskpilot.agent_tasks.screenshot_agent.graph.sync_approved_items", return_value=(1, [])) as mock_sync_items:
        res = notion_sync(state)
        assert res["synced_count"] == 1
        mock_sync_items.assert_called_once()
        kwargs = mock_sync_items.call_args[1]
        assert kwargs.get("reasoning_llm") == mock_reasoning_llm
        assert kwargs.get("fallback_reasoning_llm") == mock_fallback_llm


@pytest.mark.asyncio
async def test_run_screenshot_triage_wires_reasoning_llms_to_graph_and_state(tmp_path: Path):
    """Verify run_screenshot_triage accepts and wires reasoning_llm and fallback_reasoning_llm."""
    _create_dummy_image(tmp_path / "test.png")
    settings = Settings(
        _env_file=None,
        screenshots=ScreenshotsConfig(directory=tmp_path, delete_synced_local=False),
        notion=NotionConfig(
            token="mock_token",
            screenshot_destinations=ScreenshotDestinationsConfig(work_notes_database_id="test-db"),
        ),
    )

    mock_vision_llm = MagicMock()
    mock_reasoning_llm = MagicMock()
    mock_fallback_llm = MagicMock()
    mock_client = MagicMock()

    with (
        patch("deskpilot.agent_tasks.screenshot_agent.graph.build_screenshot_triage_graph") as mock_build_graph,
        patch("deskpilot.agent_tasks.screenshot_agent.graph.introspect_database_schema", return_value={}),
    ):
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"synced_count": 1})
        mock_build_graph.return_value = mock_graph

        await run_screenshot_triage(
            settings=settings,
            llm=mock_vision_llm,
            reasoning_llm=mock_reasoning_llm,
            fallback_reasoning_llm=mock_fallback_llm,
            notion_client=mock_client,
            auto_approve=True,
        )

        mock_build_graph.assert_called_once()
        build_kwargs = mock_build_graph.call_args[1]
        assert build_kwargs.get("reasoning_llm") == mock_reasoning_llm
        assert build_kwargs.get("fallback_reasoning_llm") == mock_fallback_llm

        invoked_state = mock_graph.ainvoke.call_args[0][0]
        assert invoked_state.get("reasoning_llm") == mock_reasoning_llm
        assert invoked_state.get("fallback_reasoning_llm") == mock_fallback_llm
