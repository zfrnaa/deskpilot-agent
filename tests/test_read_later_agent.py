"""Tests for DeskPilot Notion Read-Later Agent with LangGraph & Notion API."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from deskpilot.agent_tasks.read_later_agent.graph import (
    build_read_later_graph,
    fetch_unread_items_node,
    interactive_or_action_node,
    run_read_later_flow,
    select_daily_recommendation,
    update_notion_status_node,
)
from deskpilot.agent_tasks.read_later_agent.notion_reader import (
    parse_notion_page,
    query_unread_items,
    update_item_status,
)
from deskpilot.agent_tasks.read_later_agent.state import (
    ReadLaterAgentState,
    ReadLaterItem,
    create_initial_state,
)
from deskpilot.config import NotionConfig, Settings


def test_read_later_item_model_defaults_and_fields():
    """Verify ReadLaterItem default field values and initialization."""
    item = ReadLaterItem(page_id="page_123", title="Supercharging LangGraph")

    assert item.page_id == "page_123"
    assert item.title == "Supercharging LangGraph"
    assert item.url is None
    assert item.status == "to be read"
    assert item.added_date is None
    assert item.tags == []


def test_parse_notion_page_standard_properties():
    """Verify parse_notion_page extracts Title, URL, Status, Date, and Tags."""
    raw_page = {
        "id": "page-abc-123",
        "created_time": "2026-03-01T09:00:00.000Z",
        "properties": {
            "Title": {
                "type": "title",
                "title": [
                    {"type": "text", "plain_text": "Autonomous Agents in 2026", "text": {"content": "Autonomous Agents in 2026"}}
                ],
            },
            "URL": {
                "type": "url",
                "url": "https://example.com/agents-2026",
            },
            "Status": {
                "type": "status",
                "status": {"name": "to be read"},
            },
            "Added": {
                "type": "date",
                "date": {"start": "2026-03-01"},
            },
            "Tags": {
                "type": "multi_select",
                "multi_select": [
                    {"name": "AI"},
                    {"name": "Architecture"},
                ],
            },
        },
    }

    item = parse_notion_page(raw_page)
    assert item.page_id == "page-abc-123"
    assert item.title == "Autonomous Agents in 2026"
    assert item.url == "https://example.com/agents-2026"
    assert item.status == "to be read"
    assert item.added_date == "2026-03-01"
    assert item.tags == ["AI", "Architecture"]


def test_parse_notion_page_rich_text_and_select():
    """Verify parsing when title and URL use rich_text and status/tags use select."""
    raw_page = {
        "id": "page-xyz-789",
        "created_time": "2026-02-15T12:00:00.000Z",
        "properties": {
            "Name": {
                "type": "rich_text",
                "rich_text": [
                    {"plain_text": "Evaluating LLM Orchestration"},
                ],
            },
            "Link": {
                "type": "rich_text",
                "rich_text": [
                    {"plain_text": "https://arxiv.org/abs/1234.5678"},
                ],
            },
            "Status": {
                "type": "select",
                "select": {"name": "to be read"},
            },
            "Category": {
                "type": "select",
                "select": {"name": "Research"},
            },
        },
    }

    item = parse_notion_page(raw_page)
    assert item.page_id == "page-xyz-789"
    assert item.title == "Evaluating LLM Orchestration"
    assert item.url == "https://arxiv.org/abs/1234.5678"
    assert item.status == "to be read"
    assert item.added_date == "2026-02-15T12:00:00.000Z"
    assert item.tags == ["Research"]


def test_parse_notion_page_fallbacks_and_missing_properties():
    """Verify graceful defaults when title is empty and properties are missing."""
    raw_page = {
        "id": "page-empty-000",
        "properties": {},
    }

    item = parse_notion_page(raw_page)
    assert item.page_id == "page-empty-000"
    assert item.title == "Untitled"
    assert item.url is None
    assert item.status == "to be read"
    assert item.added_date is None
    assert item.tags == []


@pytest.mark.asyncio
async def test_query_unread_items_success():
    """Verify query_unread_items queries Notion database and parses items."""
    mock_client = MagicMock()
    mock_client.databases.query.return_value = {
        "results": [
            {
                "id": "p1",
                "properties": {
                    "Title": {"type": "title", "title": [{"plain_text": "Article 1"}]},
                    "Status": {"type": "status", "status": {"name": "to be read"}},
                },
            },
            {
                "id": "p2",
                "properties": {
                    "Title": {"type": "title", "title": [{"plain_text": "Article 2"}]},
                    "Status": {"type": "status", "status": {"name": "read"}},
                },
            },
        ]
    }

    items = await query_unread_items(database_id="db_read_later", client=mock_client)

    assert len(items) == 1
    assert items[0].page_id == "p1"
    assert items[0].title == "Article 1"
    mock_client.databases.query.assert_called_once()
    call_kwargs = mock_client.databases.query.call_args[1]
    assert call_kwargs["database_id"] == "db_read_later"


@pytest.mark.asyncio
async def test_query_unread_items_missing_client_or_database_id():
    """Verify missing client or database_id returns empty list without error."""
    assert await query_unread_items(database_id="", client=MagicMock()) == []
    assert await query_unread_items(database_id="db123", client=None) == []


@pytest.mark.asyncio
async def test_query_unread_items_api_error_handling():
    """Verify Notion API exception is handled safely and returns empty list."""
    mock_client = MagicMock()
    mock_client.databases.query.side_effect = RuntimeError("Notion rate limit exceeded")

    items = await query_unread_items(database_id="db_error", client=mock_client)
    assert items == []


@pytest.mark.asyncio
async def test_update_item_status_success():
    """Verify update_item_status calls client.pages.update with status property."""
    mock_client = MagicMock()
    mock_client.pages.update.return_value = {"id": "page_123"}

    success = await update_item_status(page_id="page_123", new_status="read", client=mock_client)
    assert success is True
    mock_client.pages.update.assert_called_once_with(
        page_id="page_123",
        properties={"Status": {"status": {"name": "read"}}},
    )


@pytest.mark.asyncio
async def test_update_item_status_handles_failure():
    """Verify update_item_status returns False on API exception."""
    mock_client = MagicMock()
    mock_client.pages.update.side_effect = ConnectionError("Failed to reach Notion")

    success = await update_item_status(page_id="page_fail", new_status="read", client=mock_client)
    assert success is False


@pytest.mark.asyncio
async def test_update_item_status_missing_args():
    """Verify update_item_status returns False if page_id or client is missing."""
    assert await update_item_status(page_id="", client=MagicMock()) is False
    assert await update_item_status(page_id="p1", client=None) is False


def test_select_daily_recommendation_empty():
    """Verify select_daily_recommendation returns None when unread list is empty."""
    state: ReadLaterAgentState = create_initial_state(database_id="db1")
    result = select_daily_recommendation(state)
    assert result["recommended_item"] is None


def test_select_daily_recommendation_oldest_first():
    """Verify select_daily_recommendation picks the oldest item by added_date."""
    item_new = ReadLaterItem(page_id="p1", title="New Article", added_date="2026-03-05")
    item_old = ReadLaterItem(page_id="p2", title="Old Article", added_date="2026-01-10")
    item_mid = ReadLaterItem(page_id="p3", title="Mid Article", added_date="2026-02-15")

    state: ReadLaterAgentState = {
        "database_id": "db1",
        "unread_items": [item_new, item_old, item_mid],
        "recommended_item": None,
        "marked_as_read_id": None,
        "status_updated": False,
        "errors": [],
    }

    result = select_daily_recommendation(state)
    assert result["recommended_item"] is not None
    assert result["recommended_item"].page_id == "p2"
    assert result["recommended_item"].title == "Old Article"


def test_select_daily_recommendation_priority_tag_overrides_date():
    """Verify items tagged with Priority take precedence over older non-priority items."""
    item_old = ReadLaterItem(page_id="p1", title="Old Regular", added_date="2026-01-01", tags=["General"])
    item_priority = ReadLaterItem(page_id="p2", title="Urgent Paper", added_date="2026-03-01", tags=["Priority", "AI"])

    state: ReadLaterAgentState = {
        "database_id": "db1",
        "unread_items": [item_old, item_priority],
        "recommended_item": None,
        "marked_as_read_id": None,
        "status_updated": False,
        "errors": [],
    }

    result = select_daily_recommendation(state)
    assert result["recommended_item"] is not None
    assert result["recommended_item"].page_id == "p2"
    assert result["recommended_item"].title == "Urgent Paper"


def test_interactive_or_action_node_programmatic_skip():
    """Verify programmatic mode without auto_mark_read leaves marked_as_read_id None."""
    item = ReadLaterItem(page_id="p1", title="Article 1")
    state: ReadLaterAgentState = {
        "database_id": "db1",
        "unread_items": [item],
        "recommended_item": item,
        "marked_as_read_id": None,
        "status_updated": False,
        "errors": [],
        "auto_mark_read": False,
    }

    result = interactive_or_action_node(state)
    assert result["marked_as_read_id"] is None


def test_interactive_or_action_node_auto_mark_read():
    """Verify auto_mark_read=True automatically flags recommended item for marking as read."""
    item = ReadLaterItem(page_id="p1", title="Article 1")
    state: ReadLaterAgentState = {
        "database_id": "db1",
        "unread_items": [item],
        "recommended_item": item,
        "marked_as_read_id": None,
        "status_updated": False,
        "errors": [],
        "auto_mark_read": True,
    }

    result = interactive_or_action_node(state)
    assert result["marked_as_read_id"] == "p1"


def test_interactive_or_action_node_with_action_func():
    """Verify action_func callback controls whether item is marked as read."""
    item = ReadLaterItem(page_id="p_cb", title="Callback Article")
    state: ReadLaterAgentState = {
        "database_id": "db1",
        "unread_items": [item],
        "recommended_item": item,
        "marked_as_read_id": None,
        "status_updated": False,
        "errors": [],
    }

    # Action func returns 'r' (mark read)
    res_read = interactive_or_action_node(state, action_func=lambda rec: "r")
    assert res_read["marked_as_read_id"] == "p_cb"

    # Action func returns 'skip'
    res_skip = interactive_or_action_node(state, action_func=lambda rec: "skip")
    assert res_skip["marked_as_read_id"] is None


@pytest.mark.asyncio
async def test_update_notion_status_node_success_and_skip():
    """Verify update_notion_status_node executes status update when requested."""
    mock_client = MagicMock()
    mock_client.pages.update.return_value = {"id": "p1"}

    item = ReadLaterItem(page_id="p1", title="Article 1")
    state_to_update: ReadLaterAgentState = {
        "database_id": "db1",
        "unread_items": [item],
        "recommended_item": item,
        "marked_as_read_id": "p1",
        "status_updated": False,
        "errors": [],
    }

    res = await update_notion_status_node(state_to_update, client=mock_client)
    assert res["status_updated"] is True
    assert item.status == "read"

    # When marked_as_read_id is None, it safely does nothing
    state_skip: ReadLaterAgentState = {
        "database_id": "db1",
        "unread_items": [item],
        "recommended_item": item,
        "marked_as_read_id": None,
        "status_updated": False,
        "errors": [],
    }
    res_skip = await update_notion_status_node(state_skip, client=mock_client)
    assert res_skip["status_updated"] is False


@pytest.mark.asyncio
async def test_end_to_end_graph_execution():
    """Verify full LangGraph pipeline execution with mocked Notion query and update."""
    mock_client = MagicMock()
    mock_client.databases.query.return_value = {
        "results": [
            {
                "id": "page_e2e",
                "properties": {
                    "Title": {"type": "title", "title": [{"plain_text": "Full Pipeline Test"}]},
                    "URL": {"type": "url", "url": "https://example.com/e2e"},
                    "Status": {"type": "status", "status": {"name": "to be read"}},
                    "Added": {"type": "date", "date": {"start": "2026-02-01"}},
                },
            }
        ]
    }
    mock_client.pages.update.return_value = {"id": "page_e2e"}

    graph = build_read_later_graph(client=mock_client, auto_mark_read=True)

    initial_state: ReadLaterAgentState = {
        "database_id": "db_test",
        "unread_items": [],
        "recommended_item": None,
        "marked_as_read_id": None,
        "status_updated": False,
        "errors": [],
        "auto_mark_read": True,
    }

    final_state = await graph.ainvoke(initial_state)

    assert len(final_state["unread_items"]) == 1
    assert final_state["recommended_item"] is not None
    assert final_state["recommended_item"].page_id == "page_e2e"
    assert final_state["marked_as_read_id"] == "page_e2e"
    assert final_state["status_updated"] is True
    assert final_state["errors"] == []


@pytest.mark.asyncio
async def test_run_read_later_flow_unconfigured_graceful_degradation():
    """Verify run_read_later_flow degrades gracefully when token or database_id is missing."""
    unconfigured_settings = Settings(
        notion=NotionConfig(token="", read_later_database_id=""),
    )

    state = await run_read_later_flow(settings=unconfigured_settings)
    assert len(state["errors"]) >= 1
    assert "not configured" in state["errors"][0].lower()
    assert state["status_updated"] is False
    assert state["recommended_item"] is None


@pytest.mark.asyncio
async def test_run_read_later_flow_with_mock_client():
    """Verify run_read_later_flow executes properly with mock client and configured settings."""
    settings = Settings(
        notion=NotionConfig(token="secret_notion_token", read_later_database_id="db_configured"),
    )

    mock_client = MagicMock()
    mock_client.databases.query.return_value = {
        "results": [
            {
                "id": "page_top",
                "properties": {
                    "Title": {"type": "title", "title": [{"plain_text": "Top Story"}]},
                    "Status": {"type": "status", "status": {"name": "to be read"}},
                },
            }
        ]
    }

    state = await run_read_later_flow(
        settings=settings,
        client=mock_client,
        auto_mark_read=False,
    )

    assert len(state["unread_items"]) == 1
    assert state["recommended_item"] is not None
    assert state["recommended_item"].title == "Top Story"
    assert state["marked_as_read_id"] is None
    assert state["status_updated"] is False


def test_no_utf8_bom_in_read_later_files():
    """Verify all Python files in read_later_agent and test files have no UTF-8 BOM."""
    base_dir = Path(__file__).resolve().parent.parent
    paths_to_check = [
        base_dir / "tests" / "test_read_later_agent.py",
        base_dir / "deskpilot" / "agent_tasks" / "read_later_agent" / "__init__.py",
        base_dir / "deskpilot" / "agent_tasks" / "read_later_agent" / "state.py",
        base_dir / "deskpilot" / "agent_tasks" / "read_later_agent" / "notion_reader.py",
        base_dir / "deskpilot" / "agent_tasks" / "read_later_agent" / "graph.py",
    ]

    for p in paths_to_check:
        if p.exists():
            content = p.read_bytes()
            assert not content.startswith(b"\xef\xbb\xbf"), f"File {p} starts with UTF-8 BOM"


def test_lazy_loading_of_langgraph_and_read_later_agent():
    """Verify boot_tasks and cli imports do not eagerly import langgraph or notion_client."""
    import subprocess
    import sys

    code = (
        "import sys; "
        "import deskpilot.cli; "
        "import deskpilot.boot_tasks; "
        "assert 'langgraph' not in sys.modules, 'langgraph eagerly imported!'; "
        "assert 'notion_client' not in sys.modules, 'notion_client eagerly imported!'"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, f"Lazy loading check failed: {result.stderr}"
