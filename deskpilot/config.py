"""Configuration models and loader for DeskPilot."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


class TempCleanerConfig(BaseModel):
    """Configuration for system temporary file cleaner."""

    enabled: bool = True
    max_age_hours: int = 24
    temp_path: Path | None = None

    def get_resolved_temp_path(self) -> Path:
        """Return the resolved path to clean, defaulting to system temp."""
        if self.temp_path is not None:
            return self.temp_path.expanduser().resolve()
        return Path(tempfile.gettempdir()).resolve()


class FloorpConfig(BaseModel):
    """Configuration for Floorp browser bookmark checks."""

    enabled: bool = True
    profile_path: Path | None = None

    def get_resolved_profile_path(self) -> Path | None:
        """Return resolved profile path or find default Floorp profile directory."""
        if self.profile_path is not None:
            return self.profile_path.expanduser().resolve()
        appdata = os.getenv("APPDATA")
        if appdata:
            default_path = Path(appdata) / "Floorp" / "Profiles"
            if default_path.exists():
                return default_path.resolve()
        return None


class CalendarConfig(BaseModel):
    """Configuration for Google Calendar briefing."""

    enabled: bool = True
    credentials_path: Path = Path("credentials.json")
    token_path: Path = Path("token.json")
    calendar_id: str = "primary"
    max_results: int = 20

    def get_resolved_credentials_path(self) -> Path:
        """Return resolved path to Google OAuth credentials.json."""
        return self.credentials_path.expanduser().resolve()

    def get_resolved_token_path(self) -> Path:
        """Return resolved path to stored Google OAuth token.json."""
        return self.token_path.expanduser().resolve()


class ScreenshotDestinationsConfig(BaseModel):
    """Configuration for Notion screenshot multi-destination routing targets."""

    work_notes_database_id: str = ""
    due_diligence_page_id: str = ""
    brainstorm_page_id: str = ""


class NotionConfig(BaseModel):
    """Configuration for Notion integrations (screenshots & read-later)."""

    enabled: bool = True
    token: str = ""
    parent_page_id: str = ""
    read_later_database_id: str = ""
    screenshot_destinations: ScreenshotDestinationsConfig = Field(
        default_factory=ScreenshotDestinationsConfig
    )


class ScreenshotsConfig(BaseModel):
    """Configuration for screenshot triage agent."""

    enabled: bool = True
    directory: Path = Path("~/Pictures/Screenshots")
    delete_synced_local: bool = True
    max_images: int = 50

    def get_resolved_directory(self) -> Path:
        """Return resolved path to the screenshots folder."""
        return self.directory.expanduser().resolve()


class DownloadsConfig(BaseModel):
    """Configuration for downloads hygiene agent."""

    enabled: bool = True
    directory: Path = Path("~/Downloads")
    stale_days: int = 30

    def get_resolved_directory(self) -> Path:
        """Return resolved path to the user downloads folder."""
        return self.directory.expanduser().resolve()


class WingetConfig(BaseModel):
    """Configuration for winget package updates check."""

    enabled: bool = True
    timeout_secs: int = 15
    ignore_packages: list[str] = Field(
        default_factory=lambda: ["IObit.AdvancedSystemCare", "IObit.DriverBooster", "RevoUninstaller.RevoUninstallerPro"]
    )


class OllamaConfig(BaseModel):
    """Configuration for local Ollama LLM / Vision model."""

    model: str = "minicpm-v"
    reasoning_model: str = "qwen2.5:3b"
    url: str = "http://localhost:11434"



class MappedSettingsSource(PydanticBaseSettingsSource):
    """Wraps a settings source to map flat notion_* variables to nested notion dictionary."""

    def __init__(self, source: PydanticBaseSettingsSource) -> None:
        super().__init__(source.settings_cls)
        self.source = source

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return self.source.get_field_value(field, field_name)

    def __call__(self) -> dict[str, Any]:
        data = self.source()
        if not isinstance(data, dict):
            return data
        notion = data.setdefault("notion", {})
        if not isinstance(notion, dict):
            notion = {}
            data["notion"] = notion
        for flat, nested in [
            ("notion_token", "token"),
            ("notion_parent_page_id", "parent_page_id"),
            ("notion_read_later_database_id", "read_later_database_id"),
        ]:
            if flat in data and nested not in notion:
                notion[nested] = data[flat]
            if nested in notion and flat not in data:
                data[flat] = notion[nested]

        destinations = notion.setdefault("screenshot_destinations", {})
        if not isinstance(destinations, dict):
            destinations = {}
            notion["screenshot_destinations"] = destinations
        for flat, dest_key in [
            ("notion_work_notes_database_id", "work_notes_database_id"),
            ("notion_due_diligence_page_id", "due_diligence_page_id"),
            ("notion_brainstorm_page_id", "brainstorm_page_id"),
        ]:
            if flat in data and dest_key not in destinations:
                destinations[dest_key] = data[flat]
            if dest_key in destinations and flat not in data:
                data[flat] = destinations[dest_key]

        return data


class Settings(BaseSettings):
    """Global configuration settings for DeskPilot."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    temp_cleaner: TempCleanerConfig = Field(default_factory=TempCleanerConfig)
    floorp: FloorpConfig = Field(default_factory=FloorpConfig)
    calendar: CalendarConfig = Field(default_factory=CalendarConfig)
    notion: NotionConfig = Field(default_factory=NotionConfig)
    screenshots: ScreenshotsConfig = Field(default_factory=ScreenshotsConfig)
    downloads: DownloadsConfig = Field(default_factory=DownloadsConfig)
    winget: WingetConfig = Field(default_factory=WingetConfig)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)

    # Top-level API keys and telemetry settings
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "DeskPilot"
    langsmith_tracing: bool | None = None
    langsmith_api_key: str = ""
    langsmith_project: str = ""

    # Flat Notion credentials support (e.g. from .env or os.environ)
    notion_token: str = ""
    notion_parent_page_id: str = ""
    notion_read_later_database_id: str = ""
    notion_work_notes_database_id: str = ""
    notion_due_diligence_page_id: str = ""
    notion_brainstorm_page_id: str = ""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        custom_yaml = getattr(settings_cls, "_custom_yaml_file", None)
        yaml_path = custom_yaml
        if yaml_path is None and Path("config.yaml").exists():
            yaml_path = Path("config.yaml")

        sources: list[PydanticBaseSettingsSource] = [
            init_settings,
            MappedSettingsSource(env_settings),
        ]
        if custom_yaml and Path(custom_yaml).exists():
            # Explicitly specified config file overrides .env settings
            sources.append(YamlConfigSettingsSource(settings_cls, yaml_file=custom_yaml))
            sources.append(MappedSettingsSource(dotenv_settings))
        else:
            # Default config.yaml acts as a fallback below .env settings
            sources.append(MappedSettingsSource(dotenv_settings))
            if yaml_path and Path(yaml_path).exists():
                sources.append(YamlConfigSettingsSource(settings_cls, yaml_file=yaml_path))
        sources.append(file_secret_settings)
        return tuple(sources)

    @model_validator(mode="after")
    def populate_flat_env_vars(self) -> Settings:
        """Populate nested credentials from flat fields or environment variables if unset."""
        if not self.notion.token:
            if self.notion_token:
                self.notion.token = self.notion_token
            elif os.getenv("NOTION_TOKEN"):
                self.notion.token = os.getenv("NOTION_TOKEN", "")
        if not self.notion_token and self.notion.token:
            self.notion_token = self.notion.token

        if not self.notion.parent_page_id:
            if self.notion_parent_page_id:
                self.notion.parent_page_id = self.notion_parent_page_id
            elif os.getenv("NOTION_PARENT_PAGE_ID"):
                self.notion.parent_page_id = os.getenv("NOTION_PARENT_PAGE_ID", "")
        if not self.notion_parent_page_id and self.notion.parent_page_id:
            self.notion_parent_page_id = self.notion.parent_page_id

        if not self.notion.read_later_database_id:
            if self.notion_read_later_database_id:
                self.notion.read_later_database_id = self.notion_read_later_database_id
            elif os.getenv("NOTION_READ_LATER_DATABASE_ID"):
                self.notion.read_later_database_id = os.getenv("NOTION_READ_LATER_DATABASE_ID", "")
        if not self.notion_read_later_database_id and self.notion.read_later_database_id:
            self.notion_read_later_database_id = self.notion.read_later_database_id

        if not self.notion.screenshot_destinations.work_notes_database_id:
            if self.notion_work_notes_database_id:
                self.notion.screenshot_destinations.work_notes_database_id = self.notion_work_notes_database_id
            elif os.getenv("NOTION_WORK_NOTES_DATABASE_ID"):
                self.notion.screenshot_destinations.work_notes_database_id = os.getenv(
                    "NOTION_WORK_NOTES_DATABASE_ID", ""
                )
        if not self.notion_work_notes_database_id and self.notion.screenshot_destinations.work_notes_database_id:
            self.notion_work_notes_database_id = self.notion.screenshot_destinations.work_notes_database_id

        if not self.notion.screenshot_destinations.due_diligence_page_id:
            if self.notion_due_diligence_page_id:
                self.notion.screenshot_destinations.due_diligence_page_id = self.notion_due_diligence_page_id
            elif os.getenv("NOTION_DUE_DILIGENCE_PAGE_ID"):
                self.notion.screenshot_destinations.due_diligence_page_id = os.getenv(
                    "NOTION_DUE_DILIGENCE_PAGE_ID", ""
                )
        if not self.notion_due_diligence_page_id and self.notion.screenshot_destinations.due_diligence_page_id:
            self.notion_due_diligence_page_id = self.notion.screenshot_destinations.due_diligence_page_id

        if not self.notion.screenshot_destinations.brainstorm_page_id:
            if self.notion_brainstorm_page_id:
                self.notion.screenshot_destinations.brainstorm_page_id = self.notion_brainstorm_page_id
            elif os.getenv("NOTION_BRAINSTORM_PAGE_ID"):
                self.notion.screenshot_destinations.brainstorm_page_id = os.getenv(
                    "NOTION_BRAINSTORM_PAGE_ID", ""
                )
        if not self.notion_brainstorm_page_id and self.notion.screenshot_destinations.brainstorm_page_id:
            self.notion_brainstorm_page_id = self.notion.screenshot_destinations.brainstorm_page_id

        if not self.gemini_api_key and os.getenv("GEMINI_API_KEY"):
            self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")

        # Harmonize LangSmith / LangChain tracing configurations
        if self.langsmith_tracing is None and "LANGSMITH_TRACING" in os.environ:
            val = os.getenv("LANGSMITH_TRACING", "").strip().lower()
            self.langsmith_tracing = val in {"true", "1", "yes"}

        if self.langsmith_tracing is not None:
            # Sync to LANGCHAIN_TRACING_V2
            self.langchain_tracing_v2 = bool(self.langsmith_tracing)
        elif self.langchain_tracing_v2:
            self.langsmith_tracing = True

        if "LANGSMITH_API_KEY" in os.environ:
            self.langsmith_api_key = os.environ["LANGSMITH_API_KEY"]
            self.langchain_api_key = self.langsmith_api_key
        elif self.langsmith_api_key:
            self.langchain_api_key = self.langsmith_api_key
        elif self.langchain_api_key:
            self.langsmith_api_key = self.langchain_api_key

        if "LANGSMITH_PROJECT" in os.environ:
            self.langsmith_project = os.environ["LANGSMITH_PROJECT"]
            self.langchain_project = self.langsmith_project
        elif self.langsmith_project:
            self.langchain_project = self.langsmith_project
        elif self.langchain_project and not self.langsmith_project:
            self.langsmith_project = self.langchain_project

        return self

    def setup_tracing(self) -> bool:
        """Export LangSmith / LangChain tracing environment variables if enabled.

        Returns True if tracing is active and configured.
        """
        is_tracing = bool(self.langsmith_tracing or self.langchain_tracing_v2)
        api_key = self.langsmith_api_key or self.langchain_api_key
        project = self.langsmith_project or self.langchain_project or "DeskPilot"

        if is_tracing:
            os.environ["LANGSMITH_TRACING"] = "true"
            os.environ["LANGCHAIN_TRACING_V2"] = "true"
            if api_key:
                os.environ["LANGSMITH_API_KEY"] = api_key
                os.environ["LANGCHAIN_API_KEY"] = api_key
            if project:
                os.environ["LANGSMITH_PROJECT"] = project
                os.environ["LANGCHAIN_PROJECT"] = project
            return True
        return False

    def get_langsmith_project_url(self) -> str:
        """Return the LangSmith project dashboard URL."""
        project = self.langsmith_project or self.langchain_project or "DeskPilot"
        return f"https://smith.langchain.com/projects/p/{project}"

    @classmethod
    def load(
        cls,
        config_path: Path | str | None = None,
        env_file: Path | str | None = None,
    ) -> Settings:
        """Load settings with an optional custom YAML config path and .env file."""
        if config_path is not None:
            path_obj = Path(config_path)
            if not path_obj.is_file():
                raise FileNotFoundError(f"Configuration file not found: {config_path}")

        original_yaml = getattr(cls, "_custom_yaml_file", None)
        try:
            if config_path is not None:
                cls._custom_yaml_file = Path(config_path)
            kwargs: dict[str, Any] = {}
            if env_file is not None:
                kwargs["_env_file"] = env_file
            return cls(**kwargs)
        finally:
            cls._custom_yaml_file = original_yaml


def load_settings(
    config_path: Path | str | None = None,
    env_file: Path | str | None = None,
) -> Settings:
    """Load application settings from YAML configuration and environment variables."""
    return Settings.load(config_path=config_path, env_file=env_file)