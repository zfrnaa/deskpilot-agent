"""Unit tests for Notion ReAct Tools and Consolidation Logic in Screenshot Agent."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, ToolCall

from deskpilot.agent_tasks.screenshot_agent.react_agent import (
    append_to_page,
    create_database_page,
    create_notion_react_tools,
    run_react_consolidation_agent,
    search_notion,
)
from deskpilot.agent_tasks.screenshot_agent.state import ScreenshotItem


@pytest.fixture
def mock_notion_client():
    client = MagicMock()
    # Mock databases.retrieve / query
    client.databases.retrieve.return_value = {
        "id": "test-db-id",
        "properties": {
            "Name": {"id": "title", "type": "title"},
            "Subject": {
                "id": "subj",
                "type": "select",
                "select": {"options": [{"name": "Work"}, {"name": "Finance"}]},
            },
            "Date": {"id": "dt", "type": "date"},
        },
    }
    client.databases.query.return_value = {
        "results": [
            {
                "id": "page-123",
                "properties": {
                    "Name": {
                        "type": "title",
                        "title": [{"plain_text": "Weekly Financial Report"}],
                    },
                    "Subject": {
                        "type": "select",
                        "select": {"name": "Finance"},
                    },
                },
            },
            {
                "id": "page-456",
                "properties": {
                    "Name": {
                        "type": "title",
                        "title": [{"plain_text": "Architecture Notes"}],
                    },
                    "Subject": {
                        "type": "select",
                        "select": {"name": "Work"},
                    },
                },
            },
        ]
    }
    # Mock blocks.children.append
    client.blocks.children.append.return_value = {"results": [{"id": "b1"}]}
    # Mock pages.create
    client.pages.create.return_value = {"id": "new-page-789"}
    return client


@pytest.fixture
def sample_item(tmp_path: Path):
    img = tmp_path / "budget_2026.png"
    img.write_bytes(b"dummy")
    return ScreenshotItem(
        path=img,
        title="Q3 Budget Review",
        classification="WORK_NOTES",
        cluster_tag="Finance",
        rationale="Budget allocation chart for Q3",
    )


@pytest.fixture
def sample_schema():
    return {
        "title_prop": "Name",
        "tag_prop": "Subject",
        "tag_type": "select",
        "date_prop": "Date",
        "tag_options": ["Finance", "Work"],
    }


def test_search_notion_tool(mock_notion_client):
    """Test search_notion tool queries Notion database and filters by query/subject."""
    tools = create_notion_react_tools(
        notion_client=mock_notion_client,
        database_id="test-db-id",
        schema={},
    )
    search_tool = next(t for t in tools if t.name == "search_notion")

    # Search for Financial
    results = search_tool.invoke({"query": "Financial", "subject": ""})
    assert len(results) == 1
    assert results[0]["id"] == "page-123"
    assert results[0]["title"] == "Weekly Financial Report"
    assert results[0]["subject"] == "Finance"

    # Search by subject
    results_subj = search_tool.invoke({"query": "", "subject": "Work"})
    assert len(results_subj) == 1
    assert results_subj[0]["id"] == "page-456"
    assert results_subj[0]["title"] == "Architecture Notes"


def test_append_to_page_tool(mock_notion_client):
    """Test append_to_page tool appends heading, rationale, and image block."""
    tools = create_notion_react_tools(
        notion_client=mock_notion_client,
        database_id="test-db-id",
        schema={},
    )
    append_tool = next(t for t in tools if t.name == "append_to_page")

    res = append_tool.invoke({
        "page_id": "page-123",
        "section_title": "Q3 Budget Review",
        "rationale": "Budget allocation chart",
        "file_upload_id": "upload-999",
    })

    assert res is True
    mock_notion_client.blocks.children.append.assert_called_once()
    call_args = mock_notion_client.blocks.children.append.call_args
    assert call_args.kwargs["block_id"] == "page-123"
    children = call_args.kwargs["children"]
    assert any(b.get("type") == "heading_3" for b in children)
    assert any(b.get("type") == "paragraph" for b in children)
    assert any(b.get("type") == "image" for b in children)


def test_create_database_page_tool(mock_notion_client, sample_schema):
    """Test create_database_page tool creates a new Notion page and returns its ID."""
    tools = create_notion_react_tools(
        notion_client=mock_notion_client,
        database_id="test-db-id",
        schema=sample_schema,
    )
    create_tool = next(t for t in tools if t.name == "create_database_page")

    new_id = create_tool.invoke({
        "title": "New Standup Topic",
        "subject": "Work",
        "rationale": "Standup diagram",
        "file_upload_id": "upload-abc",
    })

    assert new_id == "new-page-789"
    mock_notion_client.pages.create.assert_called_once()
    payload = mock_notion_client.pages.create.call_args.kwargs
    assert payload["parent"]["database_id"] == "test-db-id"
    assert "properties" in payload
    assert payload["properties"]["Name"]["title"][0]["text"]["content"] == "New Standup Topic"


def test_run_react_consolidation_agent_decision_append(mock_notion_client, sample_item, sample_schema):
    """Test run_react_consolidation_agent appends to existing page when ReAct decides append."""
    mock_llm = MagicMock()

    # Step 1: LLM calls search_notion
    call_search = ToolCall(
        name="search_notion",
        args={"query": "Budget", "subject": "Finance"},
        id="call_1",
    )
    # Step 2: LLM calls append_to_page
    call_append = ToolCall(
        name="append_to_page",
        args={
            "page_id": "page-123",
            "section_title": sample_item.title,
            "rationale": sample_item.rationale,
            "file_upload_id": "upload-123",
        },
        id="call_2",
    )
    msg1 = AIMessage(content="", tool_calls=[call_search])
    msg2 = AIMessage(content="", tool_calls=[call_append])
    msg3 = AIMessage(content="Successfully consolidated into Weekly Financial Report.")
    mock_llm.bind_tools.return_value.invoke.side_effect = [msg1, msg2, msg3]

    result = run_react_consolidation_agent(
        item=sample_item,
        notion_client=mock_notion_client,
        database_id="test-db-id",
        schema=sample_schema,
        llm=mock_llm,
        file_upload_id="upload-123",
    )

    assert result["action"] == "append"
    assert result["page_id"] == "page-123"
    assert result["synced"] is True
    assert result["error"] is None
    assert sample_item.is_synced is True


def test_run_react_consolidation_agent_decision_create(mock_notion_client, sample_item, sample_schema):
    """Test run_react_consolidation_agent creates page when no match found."""
    mock_llm = MagicMock()

    # Step 1: Search finds nothing
    call_search = ToolCall(
        name="search_notion",
        args={"query": "Budget", "subject": "Finance"},
        id="call_1",
    )
    # Step 2: Create new page
    call_create = ToolCall(
        name="create_database_page",
        args={
            "title": sample_item.title,
            "subject": sample_item.cluster_tag,
            "rationale": sample_item.rationale,
            "file_upload_id": "upload-123",
        },
        id="call_2",
    )
    msg1 = AIMessage(content="", tool_calls=[call_search])
    msg2 = AIMessage(content="", tool_calls=[call_create])
    msg3 = AIMessage(content="Created new page for Q3 Budget Review.")
    mock_llm.bind_tools.return_value.invoke.side_effect = [msg1, msg2, msg3]

    result = run_react_consolidation_agent(
        item=sample_item,
        notion_client=mock_notion_client,
        database_id="test-db-id",
        schema=sample_schema,
        llm=mock_llm,
        file_upload_id="upload-123",
    )

    assert result["action"] == "create"
    assert result["page_id"] == "new-page-789"
    assert result["synced"] is True
    assert result["error"] is None
    assert sample_item.is_synced is True


def test_run_react_consolidation_agent_429_fallback(mock_notion_client, sample_item, sample_schema):
    """Test run_react_consolidation_agent catches 429 quota exhaustion and retries with fallback_llm."""
    primary_llm = MagicMock()
    # Primary raises 429 / resource exhausted
    primary_llm.bind_tools.return_value.invoke.side_effect = Exception(
        "429 Resource has been exhausted (e.g. check quota)."
    )

    fallback_llm = MagicMock()
    call_create = ToolCall(
        name="create_database_page",
        args={
            "title": sample_item.title,
            "subject": sample_item.cluster_tag,
            "rationale": sample_item.rationale,
            "file_upload_id": "upload-123",
        },
        id="call_fb",
    )
    fallback_llm.bind_tools.return_value.invoke.side_effect = [
        AIMessage(content="", tool_calls=[call_create]),
        AIMessage(content="Created via fallback LLM."),
    ]

    result = run_react_consolidation_agent(
        item=sample_item,
        notion_client=mock_notion_client,
        database_id="test-db-id",
        schema=sample_schema,
        llm=primary_llm,
        fallback_llm=fallback_llm,
        file_upload_id="upload-123",
    )

    assert result["action"] == "create"
    assert result["page_id"] == "new-page-789"
    assert result["synced"] is True
    assert result["error"] is None
    assert sample_item.is_synced is True


def test_run_react_consolidation_agent_graceful_fallback_on_error(
    mock_notion_client, sample_item, sample_schema
):
    """Test run_react_consolidation_agent falls back open to direct page creation when an unrecoverable error occurs."""
    failing_llm = MagicMock()
    failing_llm.bind_tools.return_value.invoke.side_effect = RuntimeError("Fatal LLM crash")

    result = run_react_consolidation_agent(
        item=sample_item,
        notion_client=mock_notion_client,
        database_id="test-db-id",
        schema=sample_schema,
        llm=failing_llm,
        fallback_llm=None,
        file_upload_id="upload-123",
    )

    # Should fail open by creating the page directly using notion_client.pages.create
    assert result["action"] == "create"
    assert result["page_id"] == "new-page-789"
    assert result["synced"] is True
    assert result["error"] is None
    assert sample_item.is_synced is True
    mock_notion_client.pages.create.assert_called_once()
