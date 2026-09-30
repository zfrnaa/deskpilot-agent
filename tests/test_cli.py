"""Unit tests for DeskPilot Rich terminal dashboard and boot orchestrator CLI."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.console import Console

from deskpilot.boot_tasks import (
    BookmarkAuditResult,
    CalendarAgendaResult,
    CalendarEventItem,
    TempCleanResult,
    WingetUpdateItem,
    WingetUpdateResult,
)
from deskpilot.cli import (
    execute_menu_action,
    main,
    main_async,
    normalize_menu_choice,
    prompt_action_menu,
    render_dashboard,
    run_phase1_boot_sequence,
)
from deskpilot.config import Settings
from deskpilot.state import BootState


@pytest.fixture
def sample_boot_state() -> BootState:
    """Create a populated BootState for dashboard rendering tests."""
    state = BootState()
    state.system_hygiene = TempCleanResult(
        bytes_freed=15728640,  # 15.0 MB
        files_removed=42,
        dirs_removed=5,
        errors=[],
    )
    state.floorp_bookmarks = BookmarkAuditResult(
        total_bookmarks=120,
        noisy_count=7,
        duplicate_groups_count=2,
    )
    state.winget_updates = WingetUpdateResult(
        total_count=2,
        updates=[
            WingetUpdateItem(
                name="Git",
                id="Git.Git",
                version="2.43.0",
                available_version="2.44.0",
            ),
            WingetUpdateItem(
                name="Neovim",
                id="Neovim.Neovim",
                version="0.9.4",
                available_version="0.10.0",
            ),
        ],
    )
    state.calendar_agenda = CalendarAgendaResult(
        events=[
            CalendarEventItem(
                id="ev1",
                summary="Daily Standup",
                start_time="09:00",
                end_time="09:30",
                is_all_day=False,
            ),
            CalendarEventItem(
                id="ev2",
                summary="Architecture Review",
                start_time="14:00",
                end_time="15:00",
                is_all_day=False,
                location="Google Meet",
            ),
        ],
        total_count=2,
        date_str="2026-09-09",
        is_configured=True,
    )
    return state


@pytest.mark.asyncio
async def test_run_phase1_boot_sequence_populates_state():
    """Verify that run_phase1_boot_sequence runs only 3 fast boot tasks and populates BootState."""
    settings = Settings()

    mock_temp = TempCleanResult(bytes_freed=1024, files_removed=3, dirs_removed=1)
    mock_winget = WingetUpdateResult(total_count=1)
    mock_cal = CalendarAgendaResult(total_count=0, is_configured=True)

    with (
        patch("deskpilot.cli.clean_temp_directory", new_callable=AsyncMock) as p_temp,
        patch("deskpilot.cli.audit_floorp_bookmarks", new_callable=AsyncMock) as p_floorp,
        patch("deskpilot.cli.check_winget_updates", new_callable=AsyncMock) as p_winget,
        patch("deskpilot.cli.fetch_today_agenda", new_callable=AsyncMock) as p_cal,
    ):
        p_temp.return_value = mock_temp
        p_winget.return_value = mock_winget
        p_cal.return_value = mock_cal

        state = await run_phase1_boot_sequence(settings)

        assert state.system_hygiene == mock_temp
        assert state.winget_updates == mock_winget
        assert state.calendar_agenda == mock_cal
        assert state.bookmarks is None
        assert state.floorp_bookmarks is None
        assert not state.has_errors()

        p_temp.assert_awaited_once_with(config=settings.temp_cleaner)
        p_winget.assert_awaited_once_with(config=settings.winget)
        p_cal.assert_awaited_once_with(config=settings.calendar)
        p_floorp.assert_not_called()


@pytest.mark.asyncio
async def test_run_phase1_boot_sequence_runs_concurrently():
    """Verify that tasks are executed concurrently via asyncio.gather."""
    settings = Settings()

    async def slow_task(*args, **kwargs):
        await asyncio.sleep(0.05)
        return MagicMock()

    with (
        patch("deskpilot.cli.clean_temp_directory", side_effect=slow_task),
        patch("deskpilot.cli.check_winget_updates", side_effect=slow_task),
        patch("deskpilot.cli.fetch_today_agenda", side_effect=slow_task),
    ):
        start = time.perf_counter()
        state = await run_phase1_boot_sequence(settings)
        elapsed = time.perf_counter() - start

        # If sequential, 3 * 0.05 = 0.15s. If concurrent, ~0.05s - 0.12s.
        assert elapsed < 0.14, f"Tasks took {elapsed:.3f}s, expected concurrent execution"
        assert state is not None


@pytest.mark.asyncio
async def test_run_phase1_boot_sequence_handles_task_exceptions():
    """Verify that an exception in one boot task does not crash other tasks and records error."""
    settings = Settings()
    mock_temp = TempCleanResult(bytes_freed=500)

    with (
        patch("deskpilot.cli.clean_temp_directory", new_callable=AsyncMock) as p_temp,
        patch("deskpilot.cli.check_winget_updates", new_callable=AsyncMock) as p_winget,
        patch("deskpilot.cli.fetch_today_agenda", new_callable=AsyncMock) as p_cal,
    ):
        p_temp.return_value = mock_temp
        p_winget.side_effect = RuntimeError("Winget query failed")
        p_cal.return_value = CalendarAgendaResult(total_count=0)

        state = await run_phase1_boot_sequence(settings)

        assert state.system_hygiene == mock_temp
        assert state.winget_updates is None
        assert state.calendar_agenda.total_count == 0
        assert state.has_errors()
        assert any("Winget query failed" in err for err in state.errors)


def test_render_dashboard_3_panel_layout_on_boot():
    """Verify that render_dashboard outputs a clean 3-panel layout when bookmarks is None."""
    state = BootState()
    state.system_hygiene = TempCleanResult(bytes_freed=15728640, files_removed=42, dirs_removed=5)
    state.winget_updates = WingetUpdateResult(
        total_count=1,
        updates=[WingetUpdateItem(name="Git", id="Git.Git", version="2.43.0", available_version="2.44.0")],
    )
    state.calendar_agenda = CalendarAgendaResult(
        events=[CalendarEventItem(id="ev1", summary="Daily Standup", start_time="09:00", end_time="09:30")],
        total_count=1,
        is_configured=True,
    )

    console = Console(record=True, width=120)
    render_dashboard(state, console=console)
    output = console.export_text()

    # Verify 3 panels exist
    assert "System Hygiene" in output or "TEMP" in output
    assert "15.0 MB" in output
    assert "42" in output

    assert "Package Updates" in output or "winget" in output
    assert "Git" in output

    assert "Calendar" in output or "Agenda" in output
    assert "Daily Standup" in output

    # Verify bookmarks panel is NOT rendered on initial boot
    assert "Floorp" not in output
    assert "Bookmarks" not in output


def test_render_dashboard_captures_all_panels_when_bookmarks_present(sample_boot_state: BootState):
    """Verify that render_dashboard outputs bookmarks panel when state.bookmarks is present."""
    console = Console(record=True, width=120)
    render_dashboard(sample_boot_state, console=console)
    output = console.export_text()

    # Verify panel titles and content exist in captured text
    assert "System Hygiene" in output or "TEMP" in output
    assert "15.0 MB" in output
    assert "42" in output  # files removed

    assert "Floorp" in output or "Bookmarks" in output
    assert "120" in output  # total bookmarks
    assert "7" in output  # noisy count

    assert "Package Updates" in output or "winget" in output
    assert "Git" in output
    assert "Neovim" in output

    assert "Calendar" in output or "Agenda" in output
    assert "Daily Standup" in output
    assert "Architecture Review" in output


def test_render_dashboard_handles_unconfigured_or_empty_state():
    """Verify that render_dashboard handles an unpopulated BootState gracefully."""
    empty_state = BootState()
    console = Console(record=True, width=120)
    render_dashboard(empty_state, console=console)
    output = console.export_text()

    assert "DeskPilot" in output
    # Check that fallback text appears for unpopulated fields
    assert "not executed" in output.lower() or "not run" in output.lower() or "disabled" in output.lower()
    # Floorp bookmarks should NOT appear when empty_state.bookmarks is None
    assert "Floorp" not in output
    assert "Bookmarks" not in output


def test_render_dashboard_displays_boot_errors():
    """Verify that boot sequence errors are rendered in a prominent error panel."""
    state = BootState()
    state.add_error("Network connection refused when querying winget")
    console = Console(record=True, width=120)
    render_dashboard(state, console=console)
    output = console.export_text()

    assert "Network connection refused" in output
    assert "Error" in output or "Warning" in output


def test_prompt_action_menu_renders_and_returns_choice():
    """Verify that prompt_action_menu prints menu table and returns user selection."""
    console = Console(record=True, width=100)
    choice = prompt_action_menu(console=console, prompt_func=lambda _: "1")

    assert choice == "1"
    output = console.export_text()
    assert "[1]" in output and "Triage Screenshots" in output
    assert "[2]" in output and "Downloads Folder Cleanup" in output
    assert "[3]" in output and "Floorp Bookmarks" in output
    assert "[4]" in output and "Upgrade Winget" in output
    assert "[5]" in output and "Notion Read-Later Digest" in output
    assert "[0]" in output and "Dismiss & Exit" in output


@pytest.mark.asyncio
async def test_execute_menu_action_exit_choice_0():
    """Verify that choice '0' returns False to terminate the menu loop."""
    console = Console(record=True, width=100)
    should_continue = await execute_menu_action("0", console=console)
    assert should_continue is False
    output = console.export_text()
    assert "Exiting DeskPilot" in output or "Goodbye" in output


@pytest.mark.asyncio
async def test_execute_menu_action_option_1_screenshot_triage():
    """Verify that choice '1' invokes run_screenshot_triage and records findings."""
    console = Console(record=True, width=100)
    mock_triage_result = {
        "items": [],
        "synced_count": 3,
        "deleted_count": 3,
        "errors": [],
    }
    with (
        patch("deskpilot.agent_tasks.screenshot_agent.graph.run_screenshot_triage", new_callable=AsyncMock) as mock_agent,
        patch("deskpilot.agent_tasks.screenshot_agent.vision.check_gemini_quota", return_value=False),
    ):
        mock_agent.return_value = mock_triage_result
        settings = Settings()
        state = BootState()
        should_continue = await execute_menu_action("1", state=state, console=console, settings=settings)
        assert should_continue is True
        mock_agent.assert_awaited_once()
        assert state.agent_findings.get("screenshot_triage") == mock_triage_result
        output = console.export_text()
        assert "Screenshot" in output
        assert "Synced: 3" in output or "3" in output


@pytest.mark.asyncio
async def test_execute_menu_action_option_1_prompts_when_credits_available():
    """Verify when Gemini has credits, user is asked [y/n] and user choosing 'n' routes to Ollama."""
    console = Console(record=True, width=100)
    mock_triage_result = {"items": [], "synced_count": 1, "deleted_count": 1, "errors": []}
    with (
        patch("deskpilot.agent_tasks.screenshot_agent.graph.run_screenshot_triage", new_callable=AsyncMock) as mock_agent,
        patch("deskpilot.agent_tasks.screenshot_agent.vision.check_gemini_quota", return_value=True),
        patch("deskpilot.agent_tasks.screenshot_agent.vision.get_ollama_vision_llm") as mock_get_ollama,
    ):
        mock_agent.return_value = mock_triage_result
        mock_ollama_instance = MagicMock()
        mock_get_ollama.return_value = mock_ollama_instance

        settings = Settings()
        state = BootState()
        # User says 'n' to using Gemini
        prompt = lambda _: "n"
        should_continue = await execute_menu_action(
            "1",
            state=state,
            console=console,
            settings=settings,
            prompt_func=prompt,
        )
        assert should_continue is True
        mock_get_ollama.assert_called_once()
        mock_agent.assert_awaited_once()
        kwargs = mock_agent.call_args[1]
        assert kwargs.get("llm") == mock_ollama_instance
        assert kwargs.get("fallback_reasoning_llm") is None
        assert kwargs.get("prompt_func") == prompt
        assert kwargs.get("console_print") == console.print
        output = console.export_text()
        assert "Using local Ollama (minicpm-v) as requested" in output


@pytest.mark.asyncio
async def test_execute_menu_action_option_1_proceeds_with_gemini_on_yes():
    """Verify when Gemini has credits and user responds 'y', Gemini model is used."""
    console = Console(record=True, width=100)
    mock_triage_result = {"items": [], "synced_count": 1, "deleted_count": 1, "errors": []}
    with (
        patch("deskpilot.agent_tasks.screenshot_agent.graph.run_screenshot_triage", new_callable=AsyncMock) as mock_agent,
        patch("deskpilot.agent_tasks.screenshot_agent.vision.check_gemini_quota", return_value=True),
        patch("deskpilot.agent_tasks.screenshot_agent.vision.get_default_vision_llm") as mock_get_gemini,
        patch("deskpilot.agent_tasks.screenshot_agent.vision.get_ollama_reasoning_llm") as mock_get_ollama_reasoning,
    ):
        mock_agent.return_value = mock_triage_result
        mock_gemini_instance = MagicMock()
        mock_ollama_reasoning_instance = MagicMock()
        mock_get_gemini.return_value = mock_gemini_instance
        mock_get_ollama_reasoning.return_value = mock_ollama_reasoning_instance

        settings = Settings()
        state = BootState()
        prompt = lambda _: "y"
        should_continue = await execute_menu_action(
            "1",
            state=state,
            console=console,
            settings=settings,
            prompt_func=prompt,
        )
        assert should_continue is True
        assert mock_get_gemini.call_count >= 1
        mock_agent.assert_awaited_once_with(
            settings,
            llm=mock_gemini_instance,
            reasoning_llm=mock_gemini_instance,
            fallback_reasoning_llm=mock_ollama_reasoning_instance,
            prompt_func=prompt,
            console_print=console.print,
        )
        output = console.export_text()
        assert "Proceeding with Gemini Vision model" in output


@pytest.mark.asyncio
async def test_execute_menu_action_option_1_skipped_notion_sync_prints_notice():
    """Verify that when Notion sync is skipped via empty input, the CLI displays the finished message."""
    console = Console(record=True, width=100)
    mock_triage_result = {
        "items": [],
        "synced_count": 0,
        "deleted_count": 0,
        "errors": ["Skipped Notion sync for shot.png (finished without passing to Notion)."],
    }
    with (
        patch("deskpilot.agent_tasks.screenshot_agent.graph.run_screenshot_triage", new_callable=AsyncMock) as mock_agent,
        patch("deskpilot.agent_tasks.screenshot_agent.vision.check_gemini_quota", return_value=False),
    ):
        mock_agent.return_value = mock_triage_result
        settings = Settings()
        state = BootState()
        should_continue = await execute_menu_action(
            "1",
            state=state,
            console=console,
            settings=settings,
        )
        assert should_continue is True
        output = console.export_text()
        assert "Finished the task without passing to Notion." in output



@pytest.mark.asyncio
async def test_execute_menu_action_option_2_downloads_hygiene():
    """Verify that choice '2' invokes run_downloads_hygiene and records findings."""
    console = Console(record=True, width=100)
    mock_downloads_result = {
        "items": [],
        "total_bytes_freed": 1048576,
        "total_files_moved": 2,
        "errors": [],
    }
    with patch(
        "deskpilot.agent_tasks.downloads_agent.graph.run_downloads_hygiene",
        new_callable=AsyncMock,
    ) as mock_agent:
        mock_agent.return_value = mock_downloads_result
        settings = Settings()
        state = BootState()
        should_continue = await execute_menu_action("2", state=state, console=console, settings=settings)
        assert should_continue is True
        mock_agent.assert_awaited_once_with(settings)
        assert state.agent_findings.get("downloads_hygiene") == mock_downloads_result
        output = console.export_text()
        assert "Downloads" in output
        assert "1.0 MB" in output or "Freed" in output


@pytest.mark.asyncio
async def test_execute_menu_action_option_3_floorp_bookmarks_preview():
    """Verify that choice '3' audits Floorp bookmarks and renders user preview."""
    console = Console(record=True, width=100)
    mock_bookmarks_result = BookmarkAuditResult(
        total_bookmarks=45,
        noisy_count=5,
        duplicate_groups_count=2,
        sample_noisy=[{"title": "http://example.com", "url": "http://example.com"}],
        sample_duplicates=[{"url": "example.com", "count": 2, "bookmarks": []}],
    )
    with patch(
        "deskpilot.cli.audit_floorp_bookmarks",
        new_callable=AsyncMock,
    ) as mock_audit:
        mock_audit.return_value = mock_bookmarks_result
        settings = Settings()
        state = BootState()
        should_continue = await execute_menu_action("3", state=state, console=console, settings=settings)
        assert should_continue is True
        mock_audit.assert_awaited_once_with(config=settings.floorp)
        assert state.floorp_bookmarks == mock_bookmarks_result
        output = console.export_text()
        assert "Floorp Bookmarks" in output or "Preview" in output
        assert "45" in output
        assert "example.com" in output


@pytest.mark.asyncio
async def test_execute_menu_action_option_4_winget_upgrade():
    """Verify that choice '4' attempts to run winget upgrade interactively."""
    console = Console(record=True, width=100)

    with patch("deskpilot.cli.subprocess.run") as mock_sub:
        should_continue = await execute_menu_action("4", console=console)
        assert should_continue is True
        mock_sub.assert_called_once()
        args = mock_sub.call_args[0][0]
        assert "winget" in args
        assert "upgrade" in args


@pytest.mark.asyncio
async def test_execute_menu_action_option_4_notifies_ignored_packages():
    """Verify that choice '4' prints a notice regarding ignored packages."""
    console = Console(record=True, width=100)
    settings = Settings()

    with patch("deskpilot.cli.subprocess.run") as mock_sub:
        should_continue = await execute_menu_action("4", console=console, settings=settings)
        assert should_continue is True
        mock_sub.assert_called_once()
        output = console.export_text()
        assert "AdvancedSystemCare" in output
        assert "RevoUninstallerPro" in output


@pytest.mark.asyncio
async def test_execute_menu_action_option_4_winget_not_found():
    """Verify graceful handling if winget executable is not found."""
    console = Console(record=True, width=100)

    with patch("deskpilot.cli.subprocess.run", side_effect=FileNotFoundError):
        should_continue = await execute_menu_action("4", console=console)
        assert should_continue is True
        output = console.export_text()
        assert "winget" in output.lower()
        assert "not found" in output.lower()


@pytest.mark.asyncio
async def test_execute_menu_action_option_5_read_later_digest():
    """Verify that choice '5' invokes run_read_later_flow and records findings."""
    console = Console(record=True, width=100)
    mock_item = MagicMock()
    mock_item.title = "Sample Read Later Article"
    mock_rl_result = {
        "database_id": "db_123",
        "unread_items": [mock_item],
        "recommended_item": mock_item,
        "marked_as_read_id": None,
        "status_updated": False,
        "errors": [],
    }
    with patch(
        "deskpilot.agent_tasks.read_later_agent.graph.run_read_later_flow",
        new_callable=AsyncMock,
    ) as mock_agent:
        mock_agent.return_value = mock_rl_result
        settings = Settings()
        state = BootState()
        should_continue = await execute_menu_action(
            "5",
            state=state,
            console=console,
            settings=settings,
            prompt_func=lambda _: "n",
        )
        assert should_continue is True
        mock_agent.assert_awaited_once_with(settings)
        assert state.agent_findings.get("read_later_digest") == mock_rl_result
        output = console.export_text()
        assert "Read-Later Digest" in output
        assert "Sample Read Later Article" in output or "Unread: 1" in output


@pytest.mark.asyncio
async def test_execute_menu_action_invalid_choice():
    """Verify that an unrecognized option displays an error and continues loop."""
    console = Console(record=True, width=100)
    should_continue = await execute_menu_action("99", console=console)
    assert should_continue is True
    output = console.export_text()
    assert "Invalid option" in output or "invalid" in output.lower()


@pytest.mark.asyncio
async def test_main_async_interactive_loop_and_clean_exit():
    """Verify main_async orchestrates boot sequence, renders dashboard, and loops until exit."""
    settings = Settings()
    console = Console(record=True, width=120)

    inputs = iter(["invalid_key", "1", "0"])

    with (
        patch("deskpilot.cli.load_settings", return_value=settings),
        patch("deskpilot.cli.run_phase1_boot_sequence") as mock_boot,
        patch("deskpilot.agent_tasks.screenshot_agent.graph.run_screenshot_triage", new_callable=AsyncMock) as mock_triage,
    ):
        mock_triage.return_value = {"items": [], "synced_count": 0, "deleted_count": 0, "errors": []}
        mock_boot.return_value = BootState()
        exit_code = await main_async(
            console=console,
            settings=settings,
            prompt_func=lambda _: next(inputs),
        )

        assert exit_code == 0
        mock_boot.assert_awaited_once_with(settings)
        output = console.export_text()
        assert "DeskPilot" in output
        assert "Invalid option" in output or "invalid" in output.lower()
        assert "Exiting DeskPilot" in output or "Goodbye" in output


@pytest.mark.asyncio
async def test_main_async_handles_keyboard_interrupt():
    """Verify main_async catches KeyboardInterrupt / EOFError gracefully and returns 0."""
    settings = Settings()
    console = Console(record=True, width=120)

    def raise_interrupt(_: str) -> str:
        raise KeyboardInterrupt()

    with (
        patch("deskpilot.cli.load_settings", return_value=settings),
        patch("deskpilot.cli.run_phase1_boot_sequence", return_value=BootState()),
    ):
        exit_code = await main_async(
            console=console,
            settings=settings,
            prompt_func=raise_interrupt,
        )
        assert exit_code == 0
        output = console.export_text()
        assert "Exiting" in output or "Dismissed" in output


def test_main_entrypoint_calls_main_async_and_sys_exit():
    """Verify that main() invokes main_async and exits cleanly."""
    def fake_run(coro):
        coro.close()
        return 0

    with (
        patch("deskpilot.cli.asyncio.run", side_effect=fake_run) as mock_run,
        patch("deskpilot.cli.sys.exit") as mock_exit,
    ):
        main()
        mock_run.assert_called_once()
        mock_exit.assert_called_once_with(0)


def test_normalize_menu_choice():
    """Verify that normalize_menu_choice handles whitespace, brackets, and Windows BOM."""
    assert normalize_menu_choice("0") == "0"
    assert normalize_menu_choice("  1  ") == "1"
    assert normalize_menu_choice("[2]") == "2"
    assert normalize_menu_choice("3.") == "3"
    assert normalize_menu_choice("4") == "4"
    assert normalize_menu_choice("\ufeff0") == "0"
    assert normalize_menu_choice("\xef\xbb\xbf0") == "0"
    assert normalize_menu_choice("invalid") == "invalid"
    assert normalize_menu_choice("99") == "99"


def test_no_utf8_bom_in_cli_files():
    """Verify that CLI source and test files do not contain UTF-8 BOM headers."""
    root_dir = Path(__file__).parent.parent
    files_to_check = [
        root_dir / "deskpilot" / "cli.py",
        root_dir / "tests" / "test_cli.py",
    ]
    for file_path in files_to_check:
        if file_path.exists():
            content = file_path.read_bytes()
            assert not content.startswith(b"\xef\xbb\xbf"), (
                f"File {file_path} contains a UTF-8 BOM header."
            )


@pytest.mark.asyncio
async def test_main_async_startup_flag_runs_boot_and_exits_cleanly():
    """Verify that --startup flag executes boot sequence and exits immediately without prompting."""
    settings = Settings()
    console = Console(record=True, width=120)
    prompt_mock = MagicMock()

    with (
        patch("deskpilot.cli.load_settings", return_value=settings),
        patch("deskpilot.cli.run_phase1_boot_sequence", new_callable=AsyncMock) as mock_boot,
    ):
        mock_boot.return_value = BootState()
        exit_code = await main_async(
            console=console,
            settings=settings,
            prompt_func=prompt_mock,
            startup=True,
        )

        assert exit_code == 0
        mock_boot.assert_awaited_once_with(settings)
        prompt_mock.assert_not_called()
        output = console.export_text()
        assert "DeskPilot" in output
        assert "Startup mode active" in output or "completed" in output.lower()


@pytest.mark.asyncio
async def test_main_async_startup_sends_toast():
    settings = Settings()
    with (
        patch("deskpilot.cli.load_settings", return_value=settings),
        patch("deskpilot.cli.run_phase1_boot_sequence", new_callable=AsyncMock) as mock_boot,
        patch("deskpilot.ui.notifications.send_windows_toast") as mock_toast,
    ):
        mock_boot.return_value = BootState()
        exit_code = await main_async(settings=settings, startup=True)
        assert exit_code == 0
        mock_toast.assert_called_once()
        _, kwargs = mock_toast.call_args
        assert kwargs.get("action_command") == "deskpilot://open"


@pytest.mark.asyncio
async def test_main_async_prompts_and_reauthorizes_calendar(tmp_path: Path) -> None:
    """Verify that main_async prompts to re-authorize calendar when token is invalid and credentials exist."""
    creds_file = tmp_path / "credentials.json"
    creds_file.write_text('{"installed": {}}', encoding="utf-8")

    settings = Settings()
    settings.calendar.credentials_path = creds_file
    settings.calendar.enabled = True

    initial_cal_state = CalendarAgendaResult(
        is_configured=False,
        error="Token has been expired or revoked.",
    )

    state = BootState()
    state.calendar_agenda = initial_cal_state

    new_cal_state = CalendarAgendaResult(
        events=[CalendarEventItem(id="1", summary="Morning Standup", start_time="09:00", end_time="09:30")],
        total_count=1,
        is_configured=True,
    )

    prompts_received = []

    def mock_prompt(prompt_text: str) -> str:
        prompts_received.append(prompt_text)
        if "re-authorize" in prompt_text.lower():
            return "y"
        return "0"  # Exit menu

    console = Console(record=True, width=100)

    with (
        patch("deskpilot.cli.run_phase1_boot_sequence", new_callable=AsyncMock) as mock_boot,
        patch("deskpilot.cli.run_calendar_oauth_flow") as mock_oauth,
        patch("deskpilot.cli.fetch_today_agenda", new_callable=AsyncMock) as mock_fetch,
    ):
        mock_boot.return_value = state
        mock_fetch.return_value = new_cal_state

        exit_code = await main_async(
            console=console,
            settings=settings,
            prompt_func=mock_prompt,
            startup=False,
        )

        assert exit_code == 0
        assert mock_oauth.called
        assert mock_fetch.called
        assert state.calendar_agenda.total_count == 1
        assert any("re-authorize" in p.lower() for p in prompts_received)


def test_main_entrypoint_parses_startup_argument():
    """Verify that main parses --startup argument and passes it to main_async."""
    with (
        patch("deskpilot.cli.main_async", new_callable=AsyncMock) as mock_main_async,
        patch("deskpilot.cli.sys.exit") as mock_exit,
    ):
        mock_main_async.return_value = 0
        main(["--startup"])
        mock_main_async.assert_awaited_once_with(startup=True)
        mock_exit.assert_called_once_with(0)


def test_lazy_loading_of_langgraph_in_cli():
    """Verify that importing deskpilot.cli does NOT eagerly import langgraph."""
    import subprocess
    import sys

    cmd = [
        sys.executable,
        "-c",
        "import sys; import deskpilot.cli; assert 'langgraph' not in sys.modules, 'langgraph eagerly loaded'",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"Lazy loading check failed: {res.stderr}"

