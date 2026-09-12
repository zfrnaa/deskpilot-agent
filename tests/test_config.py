from pathlib import Path
import pytest
from deskpilot.config import (
    Settings,
    TempCleanerConfig,
    FloorpConfig,
    CalendarConfig,
    NotionConfig,
    ScreenshotDestinationsConfig,
    ScreenshotsConfig,
    DownloadsConfig,
    OllamaConfig,
    WingetConfig,
    load_settings,
)
from deskpilot.state import BootState


def test_default_config_models():
    temp_cfg = TempCleanerConfig()
    assert temp_cfg.enabled is True
    assert temp_cfg.max_age_hours == 24
    assert temp_cfg.temp_path is None
    assert temp_cfg.get_resolved_temp_path().exists()

    floorp_cfg = FloorpConfig()
    assert floorp_cfg.enabled is True
    assert floorp_cfg.profile_path is None

    cal_cfg = CalendarConfig()
    assert cal_cfg.enabled is True
    assert cal_cfg.credentials_path == Path("credentials.json")
    assert cal_cfg.token_path == Path("token.json")
    assert cal_cfg.calendar_id == "primary"

    notion_cfg = NotionConfig()
    assert notion_cfg.enabled is True
    assert notion_cfg.token == ""
    assert notion_cfg.parent_page_id == ""
    assert notion_cfg.read_later_database_id == ""
    assert isinstance(notion_cfg.screenshot_destinations, ScreenshotDestinationsConfig)
    assert notion_cfg.screenshot_destinations.work_notes_database_id == ""
    assert notion_cfg.screenshot_destinations.due_diligence_page_id == ""
    assert notion_cfg.screenshot_destinations.brainstorm_page_id == ""

    screenshots_cfg = ScreenshotsConfig()
    assert screenshots_cfg.enabled is True
    assert screenshots_cfg.directory == Path("~/Pictures/Screenshots")
    assert screenshots_cfg.delete_synced_local is True
    assert isinstance(screenshots_cfg.get_resolved_directory(), Path)

    downloads_cfg = DownloadsConfig()
    assert downloads_cfg.enabled is True
    assert downloads_cfg.directory == Path("~/Downloads")
    assert downloads_cfg.stale_days == 30

    winget_cfg = WingetConfig()
    assert winget_cfg.enabled is True
    assert winget_cfg.timeout_secs == 15
    assert winget_cfg.ignore_packages == ["IObit.AdvancedSystemCare", "RevoUninstaller.RevoUninstallerPro"]

    ollama_cfg = OllamaConfig()
    assert ollama_cfg.model == "minicpm-v"
    assert ollama_cfg.url == "http://localhost:11434"


def test_winget_config_custom_ignore_packages():
    cfg = WingetConfig(ignore_packages=["CustomApp", "AnotherApp"])
    assert cfg.ignore_packages == ["CustomApp", "AnotherApp"]


def test_settings_load_from_yaml(tmp_path: Path):
    yaml_file = tmp_path / "custom_config.yaml"
    yaml_file.write_text(
        """
temp_cleaner:
  enabled: false
  max_age_hours: 48
floorp:
  enabled: false
screenshots:
  delete_synced_local: false
winget:
  timeout_secs: 30
ollama:
  model: "llava"
  url: "http://127.0.0.1:11434"
notion:
  screenshot_destinations:
    work_notes_database_id: "db_work_notes_yaml"
    due_diligence_page_id: "page_dd_yaml"
    brainstorm_page_id: "page_bs_yaml"
""",
        encoding="utf-8",
    )

    settings = load_settings(config_path=yaml_file)
    assert settings.temp_cleaner.enabled is False
    assert settings.temp_cleaner.max_age_hours == 48
    assert settings.floorp.enabled is False
    assert settings.screenshots.delete_synced_local is False
    assert settings.winget.timeout_secs == 30
    assert settings.ollama.model == "llava"
    assert settings.ollama.url == "http://127.0.0.1:11434"
    assert settings.notion.screenshot_destinations.work_notes_database_id == "db_work_notes_yaml"
    assert settings.notion.screenshot_destinations.due_diligence_page_id == "page_dd_yaml"
    assert settings.notion.screenshot_destinations.brainstorm_page_id == "page_bs_yaml"
    # Unoverridden values retain defaults
    assert settings.calendar.enabled is True


def test_settings_env_overrides(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "secret_env_notion_token")
    monkeypatch.setenv("NOTION_PARENT_PAGE_ID", "page_12345")
    monkeypatch.setenv("NOTION_READ_LATER_DATABASE_ID", "db_67890")
    monkeypatch.setenv("NOTION_WORK_NOTES_DATABASE_ID", "db_work_env")
    monkeypatch.setenv("NOTION_DUE_DILIGENCE_PAGE_ID", "page_dd_env")
    monkeypatch.setenv("NOTION_BRAINSTORM_PAGE_ID", "page_bs_env")
    monkeypatch.setenv("GEMINI_API_KEY", "ai_key_abc")
    monkeypatch.setenv("TEMP_CLEANER__MAX_AGE_HOURS", "72")

    settings = Settings()
    assert settings.notion.token == "secret_env_notion_token"
    assert settings.notion.parent_page_id == "page_12345"
    assert settings.notion.read_later_database_id == "db_67890"
    assert settings.notion.screenshot_destinations.work_notes_database_id == "db_work_env"
    assert settings.notion.screenshot_destinations.due_diligence_page_id == "page_dd_env"
    assert settings.notion.screenshot_destinations.brainstorm_page_id == "page_bs_env"
    assert settings.gemini_api_key == "ai_key_abc"
    assert settings.temp_cleaner.max_age_hours == 72


def test_boot_state_initialization():
    state = BootState()
    assert state.timestamp is not None
    assert state.system_hygiene is None
    assert state.floorp_bookmarks is None
    assert state.winget_updates is None
    assert state.calendar_agenda is None
    assert state.agent_findings == {}
    assert state.errors == []


def test_boot_state_mutation_and_helpers():
    state = BootState()
    state.system_hygiene = {"bytes_freed": 5242880, "files_removed": 42}
    state.add_error("Could not reach Winget repository")
    state.set_finding("screenshot_agent", {"synced_count": 3, "deleted_count": 3})

    assert state.system_hygiene["bytes_freed"] == 5242880
    assert len(state.errors) == 1
    assert "Winget" in state.errors[0]
    assert state.agent_findings["screenshot_agent"]["synced_count"] == 3


def test_settings_load_from_dotenv(tmp_path: Path):
    dotenv_file = tmp_path / ".env"
    dotenv_file.write_text(
        "NOTION_TOKEN=secret_dotenv_token\n"
        "NOTION_PARENT_PAGE_ID=dotenv_parent_123\n"
        "NOTION_READ_LATER_DATABASE_ID=dotenv_db_456\n"
        "GEMINI_API_KEY=dotenv_gemini_key\n",
        encoding="utf-8",
    )

    settings = Settings.load(env_file=dotenv_file)
    assert settings.notion.token == "secret_dotenv_token"
    assert settings.notion.parent_page_id == "dotenv_parent_123"
    assert settings.notion.read_later_database_id == "dotenv_db_456"
    assert settings.gemini_api_key == "dotenv_gemini_key"
    assert settings.notion_token == "secret_dotenv_token"


def test_settings_load_missing_config_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        Settings.load(config_path="missing.yaml")

    with pytest.raises(FileNotFoundError):
        load_settings(config_path=Path("non_existent_path.yaml"))


def test_no_utf8_bom_in_python_files():
    project_root = Path(__file__).parent.parent
    py_files = [
        project_root / "deskpilot" / "__init__.py",
        project_root / "deskpilot" / "config.py",
        project_root / "deskpilot" / "state.py",
        project_root / "tests" / "test_config.py",
    ]
    for py_file in py_files:
        raw_bytes = py_file.read_bytes()
        assert not raw_bytes.startswith(b"\xef\xbb\xbf"), f"BOM found in {py_file}"


def test_settings_langsmith_tracing(monkeypatch):
    import os

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_pt_test_key_123")
    monkeypatch.setenv("LANGSMITH_PROJECT", "TestDeskPilot")

    settings = Settings()
    assert settings.langsmith_tracing is True
    assert settings.langchain_tracing_v2 is True
    assert settings.langsmith_api_key == "lsv2_pt_test_key_123"
    assert settings.langchain_api_key == "lsv2_pt_test_key_123"
    assert settings.langsmith_project == "TestDeskPilot"
    assert settings.langchain_project == "TestDeskPilot"

    # Test setup_tracing exports to environment
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
    monkeypatch.delenv("LANGCHAIN_PROJECT", raising=False)

    activated = settings.setup_tracing()
    assert activated is True
    assert os.getenv("LANGSMITH_TRACING") == "true"
    assert os.getenv("LANGCHAIN_TRACING_V2") == "true"
    assert os.getenv("LANGSMITH_API_KEY") == "lsv2_pt_test_key_123"
    assert os.getenv("LANGCHAIN_API_KEY") == "lsv2_pt_test_key_123"
    assert os.getenv("LANGSMITH_PROJECT") == "TestDeskPilot"
    assert os.getenv("LANGCHAIN_PROJECT") == "TestDeskPilot"

