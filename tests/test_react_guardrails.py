"""Unit tests for Notion ReAct tool input guardrails and schemas."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from deskpilot.agent_tasks.screenshot_agent.react_agent import (
    AppendToPageInput,
    CreateDatabasePageInput,
    SearchNotionInput,
    create_notion_react_tools,
)


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
