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


class NotionConfig(BaseModel):
    """Configuration for Notion integrations (screenshots & read-later)."""

    enabled: bool = True
    token: str = ""
    parent_page_id: str = ""
    read_later_database_id: str = ""


class ScreenshotsConfig(BaseModel):
    """Configuration for screenshot triage agent."""

    enabled: bool = True
    directory: Path = Path("~/Pictures/Screenshots")
    delete_synced_local: bool = True

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

    # Top-level API keys and telemetry settings
    gemini_api_key: str = ""
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "DeskPilot"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        yaml_path = getattr(settings_cls, "_custom_yaml_file", None)
        if yaml_path is None and Path("config.yaml").exists():
            yaml_path = Path("config.yaml")

        sources: list[PydanticBaseSettingsSource] = [
            init_settings,
            env_settings,
            dotenv_settings,
        ]
        if yaml_path and Path(yaml_path).exists():
            sources.append(YamlConfigSettingsSource(settings_cls, yaml_file=yaml_path))
        sources.append(file_secret_settings)
        return tuple(sources)

    @model_validator(mode="after")
    def populate_flat_env_vars(self) -> Settings:
        """Populate nested credentials from standard environment variables if unset."""
        if not self.notion.token and os.getenv("NOTION_TOKEN"):
            self.notion.token = os.getenv("NOTION_TOKEN", "")
        if not self.notion.parent_page_id and os.getenv("NOTION_PARENT_PAGE_ID"):
            self.notion.parent_page_id = os.getenv("NOTION_PARENT_PAGE_ID", "")
        if not self.notion.read_later_database_id and os.getenv("NOTION_READ_LATER_DATABASE_ID"):
            self.notion.read_later_database_id = os.getenv("NOTION_READ_LATER_DATABASE_ID", "")
        if not self.gemini_api_key and os.getenv("GEMINI_API_KEY"):
            self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
        return self

    @classmethod
    def load(
        cls,
        config_path: Path | str | None = None,
        env_file: Path | str | None = None,
    ) -> Settings:
        """Load settings with an optional custom YAML config path and .env file."""
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