"""Unit tests for Notion ReAct tool input guardrails and schemas."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, ToolCall, ToolMessage
from pydantic import ValidationError

from deskpilot.agent_tasks.screenshot_agent.react_agent import (
    AppendToPageInput,
    CreateDatabasePageInput,
    SearchNotionInput,
    create_notion_react_tools,
    run_react_consolidation_agent,
)
from deskpilot.agent_tasks.screenshot_agent.state import ScreenshotItem


def test_search_notion_input_defaults():
    """Verify SearchNotionInput default values and custom assignments."""
    model = SearchNotionInput()
    assert model.query == ""
    assert model.subject == ""

    custom = SearchNotionInput(query="budget", subject="Finance")
    assert custom.query == "budget"
    assert custom.subject == "Finance"


def test_append_to_page_input_valid():
    """Verify AppendToPageInput accepts valid Notion UUIDs and preserves fields."""
    valid_id_hyphen = "3dae7e99-5fc4-814b-9c40-00b2a09dd0f4"
    model = AppendToPageInput(
        page_id=valid_id_hyphen,
        section_title="Architecture Notes",
    )
    assert model.page_id == valid_id_hyphen
    assert model.section_title == "Architecture Notes"
    assert model.rationale == ""
    assert model.file_upload_id == ""

    valid_id_hex = "3dae7e995fc4814b9c4000b2a09dd0f4"
    model_hex = AppendToPageInput(
        page_id=valid_id_hex,
        section_title="Meeting Notes",
        rationale="Important discussion",
        file_upload_id="upload-123",
    )
    assert model_hex.page_id == valid_id_hex
    assert model_hex.section_title == "Meeting Notes"
    assert model_hex.rationale == "Important discussion"
    assert model_hex.file_upload_id == "upload-123"


def test_append_to_page_input_invalid_page_id():
    """Verify AppendToPageInput rejects invalid page IDs with ValidationError."""
    with pytest.raises(ValidationError):
        AppendToPageInput(
            page_id="not-a-valid-uuid",
            section_title="Test Section",
        )

    with pytest.raises(ValidationError):
        AppendToPageInput(
            page_id="",
            section_title="Test Section",
        )

    with pytest.raises(ValidationError):
        AppendToPageInput(
            page_id="   ",
            section_title="Test Section",
        )

    with pytest.raises(ValidationError):
        AppendToPageInput(
            page_id="12345",
            section_title="Test Section",
        )


def test_append_to_page_input_empty_title():
    """Verify AppendToPageInput rejects empty or whitespace-only section titles."""
    valid_id = "3dae7e99-5fc4-814b-9c40-00b2a09dd0f4"
    with pytest.raises(ValidationError):
        AppendToPageInput(
            page_id=valid_id,
            section_title="",
        )

    with pytest.raises(ValidationError):
        AppendToPageInput(
            page_id=valid_id,
            section_title="   ",
        )


def test_create_database_page_input_valid():
    """Verify CreateDatabasePageInput accepts valid title and sets defaults."""
    model = CreateDatabasePageInput(title="New Standup Topic")
    assert model.title == "New Standup Topic"
    assert model.subject == "General"
    assert model.rationale == ""
    assert model.file_upload_id == ""

    custom = CreateDatabasePageInput(
        title="Q3 Strategy",
        subject="Finance",
        rationale="Strategy chart",
        file_upload_id="3dae7e99-5fc4-814b-9c40-00b2a09dd0f4",
    )
    assert custom.title == "Q3 Strategy"
    assert custom.subject == "Finance"
    assert custom.rationale == "Strategy chart"
    assert custom.file_upload_id == "3dae7e99-5fc4-814b-9c40-00b2a09dd0f4"


def test_create_database_page_input_empty_title():
    """Verify CreateDatabasePageInput rejects empty or whitespace-only title."""
    with pytest.raises(ValidationError):
        CreateDatabasePageInput(title="")

    with pytest.raises(ValidationError):
        CreateDatabasePageInput(title="   ")


def test_create_notion_react_tools_has_args_schema():
    """Verify create_notion_react_tools associates Pydantic input models to StructuredTools."""
    mock_client = MagicMock()
    tools = create_notion_react_tools(
        notion_client=mock_client,
        database_id="test-db-id",
        schema={},
    )
    tools_by_name = {t.name: t for t in tools}

    assert "search_notion" in tools_by_name
    assert tools_by_name["search_notion"].args_schema == SearchNotionInput

    assert "append_to_page" in tools_by_name
    assert tools_by_name["append_to_page"].args_schema == AppendToPageInput

    assert "create_database_page" in tools_by_name
    assert tools_by_name["create_database_page"].args_schema == CreateDatabasePageInput


@pytest.fixture
def mock_notion_client():
    client = MagicMock()
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
    client.databases.query.return_value = {"results": []}
    client.blocks.children.append.return_value = {"results": [{"id": "b1"}]}
    client.pages.create.return_value = {"id": "new-page-fallback-789"}
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


def test_react_loop_self_corrects_on_validation_error(
    mock_notion_client, sample_item, sample_schema
):
    """Test ReAct loop intercepts ValidationError on Turn 1, feeds ToolValidationError to LLM, and succeeds on Turn 2."""
    valid_uuid = "3dae7e99-5fc4-814b-9c40-00b2a09dd0f4"
    mock_llm = MagicMock()

    # Turn 1: Invalid page_id fails UUID validation
    call_invalid = ToolCall(
        name="append_to_page",
        args={
            "page_id": "not-a-valid-uuid",
            "section_title": "Architecture Notes",
            "rationale": "Relevant notes",
        },
        id="call_invalid_1",
    )
    msg1 = AIMessage(content="", tool_calls=[call_invalid])

    # Turn 2: LLM receives ToolValidationError in conversation history and self-corrects with valid UUID
    call_valid = ToolCall(
        name="append_to_page",
        args={
            "page_id": valid_uuid,
            "section_title": "Architecture Notes",
            "rationale": "Relevant notes",
        },
        id="call_valid_2",
    )
    msg2 = AIMessage(content="", tool_calls=[call_valid])

    mock_llm.bind_tools.return_value.invoke.side_effect = [msg1, msg2]

    result = run_react_consolidation_agent(
        item=sample_item,
        notion_client=mock_notion_client,
        database_id="test-db-id",
        schema=sample_schema,
        llm=mock_llm,
        file_upload_id="upload-123",
    )

    # Tool invocation verification: NOT called on Turn 1, called ONCE on Turn 2
    mock_notion_client.blocks.children.append.assert_called_once()
    append_kwargs = mock_notion_client.blocks.children.append.call_args.kwargs
    assert append_kwargs["block_id"] == valid_uuid

    # Verify conversation history on Turn 2 contained ToolValidationError
    assert mock_llm.bind_tools.return_value.invoke.call_count == 2
    turn2_messages = mock_llm.bind_tools.return_value.invoke.call_args_list[1][0][0]
    tool_error_msg = turn2_messages[3]
    assert isinstance(tool_error_msg, ToolMessage)
    assert tool_error_msg.tool_call_id == "call_invalid_1"
    assert "ToolValidationError" in tool_error_msg.content
    assert "append_to_page" in tool_error_msg.content

    # Final result assertions
    assert result["action"] == "append"
    assert result["page_id"] == valid_uuid
    assert result["synced"] is True
    assert result["error"] is None
    assert sample_item.is_synced is True


def test_react_loop_exhausts_turns_on_persistent_validation_errors(
    mock_notion_client, sample_item, sample_schema
):
    """Test ReAct loop terminates gracefully and fails open to direct page creation when LLM fails repeatedly."""
    mock_llm = MagicMock()

    call_invalid = ToolCall(
        name="append_to_page",
        args={
            "page_id": "not-a-valid-uuid",
            "section_title": "Architecture Notes",
        },
        id="call_bad",
    )
    # LLM repeatedly returns invalid tool call for all turns
    mock_llm.bind_tools.return_value.invoke.return_value = AIMessage(
        content="", tool_calls=[call_invalid]
    )

    result = run_react_consolidation_agent(
        item=sample_item,
        notion_client=mock_notion_client,
        database_id="test-db-id",
        schema=sample_schema,
        llm=mock_llm,
        file_upload_id="upload-123",
    )

    # ReAct loop should have exhausted all 5 turns attempting self-correction
    assert mock_llm.bind_tools.return_value.invoke.call_count == 5

    # Tool function must NEVER have been called
    mock_notion_client.blocks.children.append.assert_not_called()

    # Graceful fallback: direct database page creation succeeds without crash
    mock_notion_client.pages.create.assert_called_once()
    assert result["action"] == "create"
    assert result["page_id"] == "new-page-fallback-789"
    assert result["synced"] is True
    assert result["error"] is None
    assert sample_item.is_synced is True

