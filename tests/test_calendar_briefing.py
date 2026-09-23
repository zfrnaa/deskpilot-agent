"""Tests for DeskPilot Google Calendar agenda briefing boot task."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError
import httplib2

from deskpilot.boot_tasks.calendar_briefing import (
    CALENDAR_READONLY_SCOPE,
    CALENDAR_SCOPES,
    CalendarAgendaResult,
    CalendarEventItem,
    fetch_today_agenda,
)
from deskpilot.config import CalendarConfig


def test_calendar_models_instantiation() -> None:
    """Verify CalendarEventItem and CalendarAgendaResult field defaults and types."""
    event = CalendarEventItem(
        id="evt_1",
        summary="Test Event",
        start_time="2026-09-09T10:00:00Z",
        end_time="2026-09-09T11:00:00Z",
    )
    assert event.id == "evt_1"
    assert event.summary == "Test Event"
    assert event.start_time == "2026-09-09T10:00:00Z"
    assert event.end_time == "2026-09-09T11:00:00Z"
    assert event.is_all_day is False
    assert event.location is None
    assert event.html_link is None

    result = CalendarAgendaResult()
    assert result.events == []
    assert result.total_count == 0
    assert result.date_str == ""
    assert result.is_configured is True
    assert result.error is None


def test_calendar_readonly_scope() -> None:
    """Ensure strictly read-only calendar scope is used."""
    assert CALENDAR_READONLY_SCOPE == "https://www.googleapis.com/auth/calendar.events.readonly"
    assert CALENDAR_SCOPES == ["https://www.googleapis.com/auth/calendar.events.readonly"]


@pytest.mark.asyncio
async def test_fetch_today_agenda_timed_events_parsing_and_ordering() -> None:
    """Verify parsing timed events and ensuring chronological ordering."""
    mock_items = [
        {
            "id": "evt_late",
            "summary": "Evening Wrap-up",
            "start": {"dateTime": "2026-09-09T17:00:00+08:00"},
            "end": {"dateTime": "2026-09-09T17:30:00+08:00"},
            "location": "Room B",
            "htmlLink": "https://calendar.google.com/evt_late",
        },
        {
            "id": "evt_early",
            "summary": "Morning Standup",
            "start": {"dateTime": "2026-09-09T09:00:00+08:00"},
            "end": {"dateTime": "2026-09-09T09:30:00+08:00"},
            "location": "Google Meet",
            "htmlLink": "https://calendar.google.com/evt_early",
        },
        {
            "id": "evt_mid",
            "summary": "Project Sync",
            "start": {"dateTime": "2026-09-09T13:00:00+08:00"},
            "end": {"dateTime": "2026-09-09T14:00:00+08:00"},
            "location": None,
            "htmlLink": None,
        },
    ]

    mock_service = MagicMock()
    mock_events_resource = MagicMock()
    mock_service.events.return_value = mock_events_resource
    mock_events_resource.list.return_value.execute.return_value = {"items": mock_items}

    config = CalendarConfig(calendar_id="primary", max_results=10)
    result = await fetch_today_agenda(
        config=config,
        service=mock_service,
        target_date=date(2026, 9, 9),
    )

    assert result.is_configured is True
    assert result.error is None
    assert result.total_count == 3
    assert result.date_str == "2026-09-09"
    assert len(result.events) == 3

    # Verify chronological ordering
    assert [e.id for e in result.events] == ["evt_early", "evt_mid", "evt_late"]
    assert result.events[0].summary == "Morning Standup"
    assert result.events[0].location == "Google Meet"
    assert result.events[0].is_all_day is False
    assert result.events[1].summary == "Project Sync"
    assert result.events[2].summary == "Evening Wrap-up"


@pytest.mark.asyncio
async def test_fetch_today_agenda_all_day_events_parsing() -> None:
    """Verify parsing all-day events with date-only fields and prioritizing them in ordering."""
    mock_items = [
        {
            "id": "evt_timed",
            "summary": "Afternoon Call",
            "start": {"dateTime": "2026-09-09T15:00:00Z"},
            "end": {"dateTime": "2026-09-09T16:00:00Z"},
        },
        {
            "id": "evt_allday",
            "summary": "Company Holiday",
            "start": {"date": "2026-09-09"},
            "end": {"date": "2026-09-10"},
            "htmlLink": "https://calendar.google.com/holiday",
        },
    ]

    mock_service = MagicMock()
    mock_service.events.return_value.list.return_value.execute.return_value = {
        "items": mock_items
    }

    result = await fetch_today_agenda(
        service=mock_service,
        target_date=date(2026, 9, 9),
    )

    assert result.is_configured is True
    assert result.error is None
    assert result.total_count == 2
    # All day event should be first
    assert result.events[0].id == "evt_allday"
    assert result.events[0].is_all_day is True
    assert result.events[0].start_time == "2026-09-09"
    assert result.events[0].end_time == "2026-09-10"
    assert result.events[0].summary == "Company Holiday"

    assert result.events[1].id == "evt_timed"
    assert result.events[1].is_all_day is False


@pytest.mark.asyncio
async def test_fetch_today_agenda_query_parameters() -> None:
    """Verify list() is called with correct calendarId, singleEvents, orderBy, and ISO bounds."""
    mock_service = MagicMock()
    mock_list = mock_service.events.return_value.list
    mock_list.return_value.execute.return_value = {"items": []}

    config = CalendarConfig(calendar_id="custom_cal@group.calendar.google.com", max_results=25)
    target = date(2026, 9, 9)

    result = await fetch_today_agenda(
        config=config,
        service=mock_service,
        target_date=target,
    )

    assert result.is_configured is True
    assert result.total_count == 0
    assert mock_list.called
    kwargs = mock_list.call_args.kwargs
    assert kwargs["calendarId"] == "custom_cal@group.calendar.google.com"
    assert kwargs["singleEvents"] is True
    assert kwargs["orderBy"] == "startTime"
    assert kwargs["maxResults"] == 25
    assert "2026-09-09" in kwargs["timeMin"]
    assert "2026-09-09" in kwargs["timeMax"]


@pytest.mark.asyncio
async def test_fetch_today_agenda_disabled_config() -> None:
    """Verify disabled config returns unconfigured result without making API calls."""
    config = CalendarConfig(enabled=False)
    mock_service = MagicMock()

    result = await fetch_today_agenda(config=config, service=mock_service)

    assert result.is_configured is False
    assert result.events == []
    assert result.total_count == 0
    assert result.error is None
    mock_service.events.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_today_agenda_missing_credentials_and_token(tmp_path: Path) -> None:
    """Verify graceful degradation (is_configured=False) when token and credentials do not exist."""
    missing_creds = tmp_path / "nonexistent_credentials.json"
    missing_token = tmp_path / "nonexistent_token.json"
    config = CalendarConfig(credentials_path=missing_creds, token_path=missing_token)

    result = await fetch_today_agenda(config=config)

    assert result.is_configured is False
    assert result.events == []
    assert result.total_count == 0
    assert result.error is not None
    assert "not found" in result.error.lower()


@pytest.mark.asyncio
async def test_fetch_today_agenda_missing_token_with_credentials_present(tmp_path: Path) -> None:
    """Verify graceful degradation when credentials exist but OAuth token has not been generated and non-interactive."""
    creds_file = tmp_path / "credentials.json"
    creds_file.write_text('{"installed": {"client_id": "test"}}', encoding="utf-8")
    missing_token = tmp_path / "token.json"
    config = CalendarConfig(credentials_path=creds_file, token_path=missing_token)

    result = await fetch_today_agenda(config=config, allow_interactive=False)

    assert result.is_configured is False
    assert result.events == []
    assert result.total_count == 0
    assert result.error is not None
    assert "token" in result.error.lower()


@pytest.mark.asyncio
async def test_fetch_today_agenda_interactive_oauth_success(tmp_path: Path) -> None:
    """Verify interactive OAuth flow triggers when token is missing and interactive mode is enabled."""
    creds_file = tmp_path / "credentials.json"
    creds_file.write_text('{"installed": {"client_id": "test"}}', encoding="utf-8")
    token_file = tmp_path / "token.json"
    config = CalendarConfig(credentials_path=creds_file, token_path=token_file)

    mock_flow = MagicMock()
    mock_creds = MagicMock()
    mock_creds.valid = True
    mock_creds.to_json.return_value = '{"token": "new_oauth_token"}'
    mock_flow.run_local_server.return_value = mock_creds

    mock_service = MagicMock()
    mock_events = MagicMock()
    mock_service.events.return_value = mock_events
    mock_events.list.return_value.execute.return_value = {"items": []}

    with patch(
        "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
        return_value=mock_flow,
    ) as mock_flow_factory:
        with patch("deskpilot.boot_tasks.calendar_briefing.build", return_value=mock_service) as mock_build:
            result = await fetch_today_agenda(config=config, allow_interactive=True)

            assert result.is_configured is True
            assert result.error is None
            mock_flow_factory.assert_called_once_with(str(creds_file), scopes=CALENDAR_SCOPES)
            mock_flow.run_local_server.assert_called_once_with(port=0)
            mock_build.assert_called_once_with("calendar", "v3", credentials=mock_creds, static_discovery=False)
            assert token_file.exists()
            assert token_file.read_text(encoding="utf-8") == '{"token": "new_oauth_token"}'


@pytest.mark.asyncio
async def test_fetch_today_agenda_interactive_oauth_failure(tmp_path: Path) -> None:
    """Verify interactive OAuth failure degrades gracefully without crashing."""
    creds_file = tmp_path / "credentials.json"
    creds_file.write_text('{"installed": {"client_id": "test"}}', encoding="utf-8")
    token_file = tmp_path / "token.json"
    config = CalendarConfig(credentials_path=creds_file, token_path=token_file)

    with patch(
        "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
        side_effect=RuntimeError("Browser failed to launch"),
    ):
        result = await fetch_today_agenda(config=config, allow_interactive=True)

        assert result.is_configured is False
        assert result.events == []
        assert result.error is not None
        assert "Browser failed to launch" in result.error or "OAuth" in result.error


@pytest.mark.asyncio
async def test_fetch_today_agenda_corrupted_token(tmp_path: Path) -> None:
    """Verify invalid JSON in token.json results in is_configured=False and error message."""
    bad_token = tmp_path / "token.json"
    bad_token.write_text("NOT_VALID_JSON", encoding="utf-8")
    config = CalendarConfig(token_path=bad_token)

    result = await fetch_today_agenda(config=config)

    assert result.is_configured is False
    assert result.events == []
    assert result.total_count == 0
    assert result.error is not None


@pytest.mark.asyncio
async def test_fetch_today_agenda_expired_token_refresh_success(tmp_path: Path) -> None:
    """Verify expired token is refreshed and updated credentials are saved back to disk."""
    token_file = tmp_path / "token.json"
    token_file.write_text('{"token": "old_token", "refresh_token": "refresh_val"}', encoding="utf-8")

    mock_creds = MagicMock()
    mock_creds.valid = False
    mock_creds.expired = True
    mock_creds.refresh_token = "refresh_val"
    mock_creds.to_json.return_value = '{"token": "new_refreshed_token"}'

    def refresh_side_effect(request: object) -> None:
        mock_creds.valid = True

    mock_creds.refresh.side_effect = refresh_side_effect

    mock_service = MagicMock()
    mock_service.events.return_value.list.return_value.execute.return_value = {
        "items": [{"id": "1", "summary": "Sync", "start": {"date": "2026-09-09"}, "end": {"date": "2026-09-09"}}]
    }

    config = CalendarConfig(token_path=token_file)

    with patch("deskpilot.boot_tasks.calendar_briefing.Credentials.from_authorized_user_file", return_value=mock_creds), \
         patch("deskpilot.boot_tasks.calendar_briefing.build", return_value=mock_service):
        result = await fetch_today_agenda(config=config, target_date=date(2026, 9, 9))

    assert mock_creds.refresh.called
    assert result.is_configured is True
    assert result.total_count == 1
    assert result.events[0].summary == "Sync"
    assert token_file.read_text(encoding="utf-8") == '{"token": "new_refreshed_token"}'


@pytest.mark.asyncio
async def test_fetch_today_agenda_expired_token_refresh_failure(tmp_path: Path) -> None:
    """Verify failed token refresh degrades gracefully with is_configured=False and error."""
    token_file = tmp_path / "token.json"
    token_file.write_text('{"token": "old_token", "refresh_token": "refresh_val"}', encoding="utf-8")

    mock_creds = MagicMock()
    mock_creds.valid = False
    mock_creds.expired = True
    mock_creds.refresh_token = "refresh_val"
    mock_creds.refresh.side_effect = RefreshError("Invalid grant: Token has been expired or revoked.")

    config = CalendarConfig(token_path=token_file)

    with patch("deskpilot.boot_tasks.calendar_briefing.Credentials.from_authorized_user_file", return_value=mock_creds):
        result = await fetch_today_agenda(config=config)

    assert result.is_configured is False
    assert result.total_count == 0
    assert result.error is not None
    assert "refresh" in result.error.lower()


@pytest.mark.asyncio
async def test_fetch_today_agenda_invalid_token_no_refresh(tmp_path: Path) -> None:
    """Verify expired/invalid token without a refresh_token degrades gracefully."""
    token_file = tmp_path / "token.json"
    token_file.write_text('{"token": "expired_without_refresh"}', encoding="utf-8")

    mock_creds = MagicMock()
    mock_creds.valid = False
    mock_creds.expired = True
    mock_creds.refresh_token = None

    config = CalendarConfig(token_path=token_file)

    with patch("deskpilot.boot_tasks.calendar_briefing.Credentials.from_authorized_user_file", return_value=mock_creds):
        result = await fetch_today_agenda(config=config)

    assert result.is_configured is False
    assert result.total_count == 0
    assert result.error is not None


@pytest.mark.asyncio
async def test_fetch_today_agenda_api_http_error() -> None:
    """Verify HttpError from Google Calendar API returns error message without crashing."""
    mock_service = MagicMock()
    resp = httplib2.Response({"status": 500, "reason": "Internal Server Error"})
    mock_service.events.return_value.list.return_value.execute.side_effect = HttpError(resp, b"Internal error")

    result = await fetch_today_agenda(service=mock_service, target_date=date(2026, 9, 9))

    assert result.is_configured is True
    assert result.events == []
    assert result.total_count == 0
    assert result.error is not None
    assert "500" in result.error or "Internal Server Error" in result.error or "HttpError" in result.error


@pytest.mark.asyncio
async def test_fetch_today_agenda_api_unexpected_exception() -> None:
    """Verify unexpected runtime error during API call degrades gracefully."""
    mock_service = MagicMock()
    mock_service.events.return_value.list.return_value.execute.side_effect = ConnectionResetError("Connection lost")

    result = await fetch_today_agenda(service=mock_service, target_date=date(2026, 9, 9))

    assert result.is_configured is True
    assert result.events == []
    assert result.total_count == 0
    assert result.error is not None
    assert "Connection lost" in result.error


@pytest.mark.asyncio
async def test_fetch_today_agenda_expired_token_triggers_interactive_oauth(tmp_path: Path) -> None:
    """Verify that when token refresh fails in interactive mode, OAuth flow is re-run and succeeds."""
    token_file = tmp_path / "token.json"
    token_file.write_text('{"token": "old_token", "refresh_token": "expired_refresh"}', encoding="utf-8")
    creds_file = tmp_path / "credentials.json"
    creds_file.write_text('{"installed": {"client_id": "test_id"}}', encoding="utf-8")

    mock_creds = MagicMock()
    mock_creds.valid = False
    mock_creds.expired = True
    mock_creds.refresh_token = "expired_refresh"
    mock_creds.refresh.side_effect = RefreshError("Token expired or revoked.")

    mock_new_creds = MagicMock()
    mock_new_creds.valid = True

    mock_service = MagicMock()
    mock_service.events.return_value.list.return_value.execute.return_value = {
        "items": [{"id": "ev99", "summary": "Reauth Event", "start": {"dateTime": "2026-09-09T10:00:00Z"}, "end": {"dateTime": "2026-09-09T11:00:00Z"}}]
    }

    config = CalendarConfig(credentials_path=creds_file, token_path=token_file)

    with (
        patch("deskpilot.boot_tasks.calendar_briefing.Credentials.from_authorized_user_file", return_value=mock_creds),
        patch("deskpilot.boot_tasks.calendar_briefing.run_calendar_oauth_flow", return_value=mock_new_creds) as mock_oauth_flow,
        patch("deskpilot.boot_tasks.calendar_briefing.build", return_value=mock_service),
    ):
        result = await fetch_today_agenda(config=config, target_date=date(2026, 9, 9), allow_interactive=True)

    assert mock_oauth_flow.called
    assert result.is_configured is True
    assert result.total_count == 1
    assert result.events[0].summary == "Reauth Event"


def test_no_utf8_bom_in_calendar_files() -> None:
    """Ensure calendar source and test files do not contain UTF-8 BOM."""
    files_to_check = [
        Path("deskpilot/boot_tasks/calendar_briefing.py"),
        Path("tests/test_calendar_briefing.py"),
    ]
    for file_path in files_to_check:
        if file_path.exists():
            content = file_path.read_bytes()
            assert not content.startswith(b"\xef\xbb\xbf"), f"{file_path} contains UTF-8 BOM"
