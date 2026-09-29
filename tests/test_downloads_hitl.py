"""Unit tests for LangGraph Human-in-the-Loop breakpoints, state inspection, and time-travel resume in Downloads Hygiene Agent."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver

from deskpilot.agent_tasks.downloads_agent import (
    build_downloads_hygiene_graph,
    create_initial_state,
    get_hygiene_state,
    resume_downloads_hygiene,
)


def _set_file_age_days(path: Path, days: float) -> None:
    """Helper to set file modification time in the past."""
    mtime = time.time() - (days * 86400.0)
    os.utime(path, (mtime, mtime))


@pytest.mark.asyncio
async def test_downloads_graph_interrupts_before_execute_actions(tmp_path: Path):
    """Verify with interrupt_before=['execute_actions'] and MemorySaver, graph halts before execution."""
    stale_exe = tmp_path / "old_installer.exe"
    stale_exe.write_bytes(b"MZ_installer_binary_content")
    _set_file_age_days(stale_exe, 45.0)

    doc_orig = tmp_path / "statement.pdf"
    doc_orig.write_bytes(b"statement_pdf")

    doc_dup = tmp_path / "statement (1).pdf"
    doc_dup.write_bytes(b"duplicate_pdf")

    checkpointer = MemorySaver()
    graph = build_downloads_hygiene_graph(
        auto_approve=True,
        checkpointer=checkpointer,
        interrupt_before=["execute_actions"],
    )

    thread_id = "hitl-test-thread-001"
    config = {"configurable": {"thread_id": thread_id}}

    state = create_initial_state(
        downloads_dir=tmp_path,
        archive_dir=tmp_path / "Archive",
        installer_max_age_days=30,
        auto_approve=True,
    )

    # First invoke halts right before execute_actions
    await graph.ainvoke(state, config=config)

    # State inspection via get_hygiene_state
    snapshot = get_hygiene_state(graph, thread_id)
    assert snapshot is not None
    assert snapshot.next == ("execute_actions",)

    # Verify action plan was assembled and actions approved
    action_plan = snapshot.values.get("action_plan", {})
    assert "delete_installers" in action_plan
    assert "delete_duplicates" in action_plan

    # Verify critical safety rule: files are NOT touched before resumption
    assert stale_exe.exists(), "Stale installer must remain untouched when interrupted"
    assert doc_dup.exists(), "Duplicate file must remain untouched when interrupted"
    assert doc_orig.exists(), "Original statement file must remain untouched"
    assert not (tmp_path / "Archive").exists(), "Archive folder should not be created yet"
    assert snapshot.values.get("total_bytes_freed", 0) == 0
    assert snapshot.values.get("total_files_moved", 0) == 0


@pytest.mark.asyncio
async def test_downloads_graph_time_travel_update_state_and_resume(tmp_path: Path):
    """Verify inspecting paused state, time-travel updating approved_actions, and resuming execution."""
    stale_exe = tmp_path / "setup_v1.exe"
    stale_exe.write_bytes(b"setup_bytes" * 50)
    _set_file_age_days(stale_exe, 50.0)

    doc_orig = tmp_path / "report.pdf"
    doc_orig.write_bytes(b"report_content")

    doc_dup = tmp_path / "report (1).pdf"
    doc_dup.write_bytes(b"dup_content")

    archive_zip = tmp_path / "data.zip"
    archive_zip.write_bytes(b"zip_content")

    checkpointer = MemorySaver()
    graph = build_downloads_hygiene_graph(
        auto_approve=True,
        checkpointer=checkpointer,
        interrupt_before=["execute_actions"],
    )

    thread_id = "time-travel-thread-002"
    config = {"configurable": {"thread_id": thread_id}}

    state = create_initial_state(
        downloads_dir=tmp_path,
        archive_dir=tmp_path / "Archive",
        installer_max_age_days=30,
        auto_approve=True,
    )

    # Run up to interruption
    await graph.ainvoke(state, config=config)

    # Inspect paused state
    paused_snapshot = get_hygiene_state(graph, thread_id)
    assert paused_snapshot.next == ("execute_actions",)
    action_plan = paused_snapshot.values.get("action_plan", {})
    assert set(action_plan.keys()) == {
        "delete_installers",
        "delete_duplicates",
        "archive_archives",
        "archive_documents",
    }

    # Time travel: Human reviews and decides to ONLY approve 'delete_duplicates'
    # Overriding 'approved_actions' removes 'delete_installers', 'archive_archives', and 'archive_documents'
    resume_result = await resume_downloads_hygiene(
        graph,
        thread_id=thread_id,
        approved_actions=["delete_duplicates"],
    )

    # Verify execution finished
    post_snapshot = get_hygiene_state(graph, thread_id)
    assert post_snapshot.next == ()

    # Verify only approved action was executed
    assert not doc_dup.exists(), "Approved duplicate must be deleted"
    assert stale_exe.exists(), "Unapproved stale installer must be preserved on disk"
    assert archive_zip.exists(), "Unapproved archive must remain in downloads"
    assert not (tmp_path / "Archive" / "data.zip").exists()
    assert doc_orig.exists(), "Unapproved original document must remain in downloads"
    assert not (tmp_path / "Archive" / "report.pdf").exists()

    assert resume_result["total_bytes_freed"] == len(b"dup_content")
    assert resume_result["total_files_moved"] == 0


@pytest.mark.asyncio
async def test_downloads_graph_normal_run_without_checkpointer(tmp_path: Path):
    """Verify non-interrupted backward compatibility when checkpointer and interrupt_before are omitted."""
    stale_exe = tmp_path / "legacy_setup.exe"
    stale_exe.write_bytes(b"legacy_installer_bytes")
    _set_file_age_days(stale_exe, 40.0)

    doc_file = tmp_path / "sheet.xlsx"
    doc_file.write_bytes(b"sheet_content")

    # Call with defaults
    graph = build_downloads_hygiene_graph(auto_approve=True)

    state = create_initial_state(
        downloads_dir=tmp_path,
        archive_dir=tmp_path / "Archive",
        installer_max_age_days=30,
        auto_approve=True,
    )

    final_state = await graph.ainvoke(state)

    # Verifies standard complete flow without breakpoint
    assert not stale_exe.exists()
    assert not doc_file.exists()
    assert (tmp_path / "Archive" / "sheet.xlsx").exists()
    assert final_state["total_bytes_freed"] == len(b"legacy_installer_bytes")
    assert final_state["total_files_moved"] == 1


@pytest.mark.asyncio
async def test_downloads_graph_resume_without_actions_override(tmp_path: Path):
    """Verify resume_downloads_hygiene resumes with existing approved_actions when approved_actions is None."""
    stale_exe = tmp_path / "temp_setup.exe"
    stale_exe.write_bytes(b"temp_installer")
    _set_file_age_days(stale_exe, 40.0)

    checkpointer = MemorySaver()
    graph = build_downloads_hygiene_graph(
        auto_approve=True,
        checkpointer=checkpointer,
        interrupt_before=["execute_actions"],
    )

    thread_id = "resume-no-override-003"
    config = {"configurable": {"thread_id": thread_id}}

    state = create_initial_state(
        downloads_dir=tmp_path,
        archive_dir=tmp_path / "Archive",
        installer_max_age_days=30,
        auto_approve=True,
    )

    await graph.ainvoke(state, config=config)

    # Resume without overriding approved_actions
    resumed_result = await resume_downloads_hygiene(graph, thread_id=thread_id)

    assert not stale_exe.exists()
    assert resumed_result["total_bytes_freed"] == len(b"temp_installer")
    snapshot = get_hygiene_state(graph, thread_id)
    assert snapshot.next == ()


def test_no_utf8_bom_in_downloads_hitl_test():
    """Verify test_downloads_hitl.py has no UTF-8 BOM."""
    path = Path(__file__).resolve()
    assert not path.read_bytes().startswith(b"\xef\xbb\xbf"), "File contains UTF-8 BOM"
