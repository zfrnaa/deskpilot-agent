import os
import pytest
from deskpilot.config import Settings


def test_setup_tracing_enabled(monkeypatch):
    """Test that setup_tracing sets environment variables when tracing is enabled."""
    # Clear any ambient environment variables
    for env_var in [
        "LANGSMITH_TRACING",
        "LANGCHAIN_TRACING_V2",
        "LANGSMITH_API_KEY",
        "LANGCHAIN_API_KEY",
        "LANGSMITH_PROJECT",
        "LANGCHAIN_PROJECT",
    ]:
        monkeypatch.delenv(env_var, raising=False)

    settings = Settings(
        langsmith_tracing=True,
        langsmith_api_key="lsv2_test_api_key_456",
        langsmith_project="TestTracingProject",
    )

    activated = settings.setup_tracing()
    assert activated is True
    assert os.getenv("LANGSMITH_TRACING") == "true"
    assert os.getenv("LANGCHAIN_TRACING_V2") == "true"
    assert os.getenv("LANGSMITH_API_KEY") == "lsv2_test_api_key_456"
    assert os.getenv("LANGCHAIN_API_KEY") == "lsv2_test_api_key_456"
    assert os.getenv("LANGSMITH_PROJECT") == "TestTracingProject"
    assert os.getenv("LANGCHAIN_PROJECT") == "TestTracingProject"


def test_get_langsmith_project_url(monkeypatch):
    """Test get_langsmith_project_url with custom and fallback project names."""
    for env_var in [
        "LANGSMITH_PROJECT",
        "LANGCHAIN_PROJECT",
    ]:
        monkeypatch.delenv(env_var, raising=False)

    # 1. Custom langsmith_project
    settings_custom = Settings(langsmith_project="CustomProject")
    assert (
        settings_custom.get_langsmith_project_url()
        == "https://smith.langchain.com/projects/p/CustomProject"
    )

    # 2. Fallback to langchain_project when langsmith_project is empty
    settings_chain = Settings(langsmith_project="", langchain_project="ChainProject")
    assert (
        settings_chain.get_langsmith_project_url()
        == "https://smith.langchain.com/projects/p/ChainProject"
    )

    # 3. Default fallback to DeskPilot
    settings_default = Settings(langsmith_project="", langchain_project="")
    assert (
        settings_default.get_langsmith_project_url()
        == "https://smith.langchain.com/projects/p/DeskPilot"
    )


@pytest.mark.asyncio
async def test_screenshot_agent_tracing_metadata(tmp_path):
    """Test that run_screenshot_triage enriches graph.ainvoke with run_name, tags, and metadata."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from deskpilot.config import ScreenshotsConfig
    from deskpilot.agent_tasks.screenshot_agent.graph import run_screenshot_triage

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value={"status": "done"})

    settings = Settings(
        gemini_model="gemini-2.5-flash",
        screenshots=ScreenshotsConfig(
            directory=tmp_path,
            max_images=25,
            delete_synced_local=False,
        ),
    )

    with patch(
        "deskpilot.agent_tasks.screenshot_agent.graph.build_screenshot_triage_graph",
        return_value=mock_graph,
    ):
        await run_screenshot_triage(settings, auto_approve=True, llm=MagicMock())

    mock_graph.ainvoke.assert_called_once()
    _, call_kwargs = mock_graph.ainvoke.call_args
    config = call_kwargs.get("config", {})
    assert config.get("run_name") == "ScreenshotTriageAgent"
    assert config.get("tags") == ["agent:screenshot_triage", "model:gemini-2.5-flash"]
    assert config.get("metadata") == {
        "auto_approve": True,
        "max_images": 25,
        "delete_synced_local": False,
    }


@pytest.mark.asyncio
async def test_downloads_agent_tracing_metadata(tmp_path):
    """Test that run_downloads_hygiene enriches graph.ainvoke with run_name, tags, and metadata."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from deskpilot.config import DownloadsConfig
    from deskpilot.agent_tasks.downloads_agent.graph import run_downloads_hygiene

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value={"status": "done"})

    settings = Settings(
        downloads=DownloadsConfig(directory=tmp_path, stale_days=14),
    )

    with patch(
        "deskpilot.agent_tasks.downloads_agent.graph.build_downloads_hygiene_graph",
        return_value=mock_graph,
    ):
        await run_downloads_hygiene(settings, auto_approve=False)

    mock_graph.ainvoke.assert_called_once()
    _, call_kwargs = mock_graph.ainvoke.call_args
    config = call_kwargs.get("config", {})
    assert config.get("run_name") == "DownloadsHygieneAgent"
    assert config.get("tags") == ["agent:downloads_hygiene"]
    assert config.get("metadata") == {
        "auto_approve": False,
        "stale_days": 14,
    }


@pytest.mark.asyncio
async def test_read_later_agent_tracing_metadata():
    """Test that read later agent enriches graph.ainvoke with run_name and tags."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from deskpilot.config import NotionConfig
    from deskpilot.agent_tasks.read_later_agent.graph import (
        run_read_later_digest,
        run_read_later_flow,
    )

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value={"status": "done"})

    settings = Settings(
        notion=NotionConfig(
            enabled=True,
            token="test_notion_token",
            read_later_database_id="test_db_id",
        )
    )

    with patch(
        "deskpilot.agent_tasks.read_later_agent.graph.build_read_later_graph",
        return_value=mock_graph,
    ):
        await run_read_later_digest(settings, client=MagicMock())

    mock_graph.ainvoke.assert_called_once()
    _, call_kwargs = mock_graph.ainvoke.call_args
    config = call_kwargs.get("config", {})
    assert config.get("run_name") == "ReadLaterAgent"
    assert config.get("tags") == ["agent:read_later"]


def test_react_agent_tracing_metadata(tmp_path):
    """Test that ReAct consolidation loop passes run_name and tags in config to llm_with_tools.invoke."""
    from unittest.mock import MagicMock
    from langchain_core.messages import AIMessage
    from deskpilot.agent_tasks.screenshot_agent.react_agent import run_react_consolidation_agent
    from deskpilot.agent_tasks.screenshot_agent.state import ScreenshotItem

    mock_llm = MagicMock()
    mock_llm_with_tools = MagicMock()
    mock_llm.bind_tools.return_value = mock_llm_with_tools
    # Return AIMessage with no tool_calls so loop terminates after 1 turn
    mock_llm_with_tools.invoke.return_value = AIMessage(content="No tool calls needed")

    item = ScreenshotItem(path=tmp_path / "chart.png", filename="chart.png", title="Financial Chart")
    mock_client = MagicMock()

    run_react_consolidation_agent(
        item=item,
        notion_client=mock_client,
        database_id="test_db",
        schema={},
        llm=mock_llm,
    )

    mock_llm_with_tools.invoke.assert_called_once()
    _, call_kwargs = mock_llm_with_tools.invoke.call_args
    config = call_kwargs.get("config", {})
    assert config.get("run_name") == "ConsolidateNote_chart.png"
    assert config.get("tags") == ["agent:react_consolidation"]

