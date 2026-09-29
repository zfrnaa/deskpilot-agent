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


def test_check_tracing_disabled(monkeypatch):
    """Test check_tracing_health when tracing is disabled."""
    for env_var in [
        "LANGSMITH_TRACING",
        "LANGCHAIN_TRACING_V2",
    ]:
        monkeypatch.delenv(env_var, raising=False)

    from rich.console import Console
    from deskpilot.cli import check_tracing_health

    settings = Settings(langsmith_tracing=False, langchain_tracing_v2=False)
    console = Console(record=True, width=120)

    result = check_tracing_health(settings, console=console)
    assert result is False
    output = console.export_text()
    assert "disabled" in output.lower()
    assert ".env" in output


def test_check_tracing_missing_api_key(monkeypatch):
    """Test check_tracing_health when tracing is enabled but API key is missing."""
    for env_var in [
        "LANGSMITH_TRACING",
        "LANGCHAIN_TRACING_V2",
        "LANGSMITH_API_KEY",
        "LANGCHAIN_API_KEY",
    ]:
        monkeypatch.delenv(env_var, raising=False)

    from rich.console import Console
    from deskpilot.cli import check_tracing_health

    settings = Settings(
        langsmith_tracing=True,
        langsmith_api_key="",
        langchain_api_key="",
    )
    console = Console(record=True, width=120)

    result = check_tracing_health(settings, console=console)
    assert result is False
    output = console.export_text()
    assert "LANGSMITH_API_KEY" in output
    assert "missing" in output.lower()


def test_check_tracing_active_success(monkeypatch):
    """Test check_tracing_health when tracing is active and client connectivity succeeds."""
    for env_var in [
        "LANGSMITH_TRACING",
        "LANGCHAIN_TRACING_V2",
        "LANGSMITH_API_KEY",
        "LANGCHAIN_API_KEY",
        "LANGSMITH_PROJECT",
        "LANGCHAIN_PROJECT",
    ]:
        monkeypatch.delenv(env_var, raising=False)

    from unittest.mock import MagicMock, patch
    from rich.console import Console
    from deskpilot.cli import check_tracing_health

    settings = Settings(
        langsmith_tracing=True,
        langsmith_api_key="lsv2_test_valid_key",
        langsmith_project="TestHealthProject",
    )
    console = Console(record=True, width=120)

    mock_client = MagicMock()
    with patch("langsmith.Client", return_value=mock_client) as mock_client_cls:
        result = check_tracing_health(settings, console=console)

        assert result is True
        mock_client_cls.assert_called_once_with(api_key="lsv2_test_valid_key")
        mock_client.read_project.assert_called_once_with(project_name="TestHealthProject")
        output = console.export_text()
        assert "Connected" in output
        assert "TestHealthProject" in output
        assert "https://smith.langchain.com/projects/p/TestHealthProject" in output


def test_check_tracing_error(monkeypatch):
    """Test check_tracing_health when client raises an exception."""
    for env_var in [
        "LANGSMITH_TRACING",
        "LANGCHAIN_TRACING_V2",
        "LANGSMITH_API_KEY",
        "LANGCHAIN_API_KEY",
        "LANGSMITH_PROJECT",
        "LANGCHAIN_PROJECT",
    ]:
        monkeypatch.delenv(env_var, raising=False)

    from unittest.mock import MagicMock, patch
    from rich.console import Console
    from deskpilot.cli import check_tracing_health

    settings = Settings(
        langsmith_tracing=True,
        langsmith_api_key="lsv2_test_bad_key",
        langsmith_project="TestHealthProject",
    )
    console = Console(record=True, width=120)

    mock_client = MagicMock()
    mock_client.read_project.side_effect = ConnectionError("Could not reach LangSmith API")
    with patch("langsmith.Client", return_value=mock_client):
        result = check_tracing_health(settings, console=console)

        assert result is False
        output = console.export_text()
        assert "Could not reach LangSmith API" in output or "Failed" in output


@pytest.mark.asyncio
async def test_main_async_check_tracing_flag():
    """Test --check-tracing CLI flag via main_async."""
    from unittest.mock import patch
    from deskpilot.cli import main_async

    # 1. main_async with check_tracing=True and healthy check
    settings = Settings(langsmith_tracing=True, langsmith_api_key="lsv2_test")
    with patch("deskpilot.cli.check_tracing_health", return_value=True) as mock_health:
        exit_code = await main_async(settings=settings, check_tracing=True)
        assert exit_code == 0
        mock_health.assert_called_once()

    # 2. main_async with check_tracing=True and failing check
    with patch("deskpilot.cli.check_tracing_health", return_value=False) as mock_health:
        exit_code = await main_async(settings=settings, check_tracing=True)
        assert exit_code == 1
        mock_health.assert_called_once()


def test_main_entrypoint_parses_check_tracing_argument():
    """Test main() entrypoint parses --check-tracing and passes it to main_async."""
    from unittest.mock import AsyncMock, patch
    from deskpilot.cli import main

    with (
        patch("deskpilot.cli.main_async", new_callable=AsyncMock) as mock_main_async,
        patch("deskpilot.cli.sys.exit") as mock_exit,
    ):
        mock_main_async.return_value = 0
        main(["--check-tracing"])
        mock_main_async.assert_awaited_once_with(check_tracing=True)
        mock_exit.assert_called_once_with(0)


def test_dashboard_trace_indicator_rendered_when_enabled(monkeypatch):
    """Test that render_dashboard displays the trace indicator when tracing is enabled."""
    for env_var in [
        "LANGSMITH_TRACING",
        "LANGCHAIN_TRACING_V2",
        "LANGSMITH_API_KEY",
        "LANGCHAIN_API_KEY",
        "LANGSMITH_PROJECT",
        "LANGCHAIN_PROJECT",
    ]:
        monkeypatch.delenv(env_var, raising=False)

    from rich.console import Console
    from deskpilot.cli import render_dashboard
    from deskpilot.state import BootState

    settings = Settings(
        langsmith_tracing=True,
        langsmith_project="TestIndicatorProject",
    )
    state = BootState()
    console = Console(record=True, width=120)

    render_dashboard(state, console=console, settings=settings)
    output = console.export_text()

    assert "LangSmith Tracing:" in output
    assert "Active" in output
    assert "TestIndicatorProject" in output
    assert "https://smith.langchain.com/projects/p/TestIndicatorProject" in output


def test_dashboard_trace_indicator_omitted_when_disabled(monkeypatch):
    """Test that render_dashboard omits the trace indicator when tracing is disabled."""
    for env_var in [
        "LANGSMITH_TRACING",
        "LANGCHAIN_TRACING_V2",
        "LANGSMITH_API_KEY",
        "LANGCHAIN_API_KEY",
        "LANGSMITH_PROJECT",
        "LANGCHAIN_PROJECT",
    ]:
        monkeypatch.delenv(env_var, raising=False)

    from rich.console import Console
    from deskpilot.cli import render_dashboard
    from deskpilot.state import BootState

    settings = Settings(
        langsmith_tracing=False,
        langchain_tracing_v2=False,
    )
    state = BootState()
    console = Console(record=True, width=120)

    render_dashboard(state, console=console, settings=settings)
    output = console.export_text()

    assert "LangSmith Tracing:" not in output


