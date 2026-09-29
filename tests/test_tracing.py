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
