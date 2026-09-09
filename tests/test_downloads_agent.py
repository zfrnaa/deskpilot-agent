"""Tests for DeskPilot Downloads Hygiene Agent with LangGraph."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from deskpilot.config import DownloadsConfig, Settings
from deskpilot.agent_tasks.downloads_agent.actions import (
    assemble_action_plan,
    categorize_item,
    categorize_items,
    execute_approved_actions,
    format_plan_summary,
    scan_directory,
)
from deskpilot.agent_tasks.downloads_agent.graph import (
    build_downloads_hygiene_graph,
    categorize_and_analyze,
    execute_actions,
    human_review_node,
    propose_plan,
    run_downloads_hygiene,
    scan_downloads,
)
from deskpilot.agent_tasks.downloads_agent.state import (
    DownloadFileCategory,
    DownloadItem,
    DownloadsAgentState,
    ProposedAction,
    create_initial_state,
)


def _set_file_age_days(path: Path, days: float) -> None:
    """Helper to set file modification time in the past."""
    mtime = time.time() - (days * 86400.0)
    os.utime(path, (mtime, mtime))


def test_download_item_model_defaults_and_validation(tmp_path: Path):
    """Verify DownloadItem default fields and filename population."""
    file_path = tmp_path / "installer.exe"
    file_path.write_bytes(b"MZ" + b"\x00" * 100)

    item = DownloadItem(
        path=file_path,
        size_bytes=102,
        category=DownloadFileCategory.INSTALLER,
        age_days=15.5,
    )

    assert item.path == file_path
    assert item.filename == "installer.exe"
    assert item.size_bytes == 102
    assert item.category == DownloadFileCategory.INSTALLER
    assert item.age_days == 15.5
    assert item.proposed_action == ProposedAction.KEEP
    assert item.rationale == ""
    assert item.is_executed is False


def test_scan_directory_finds_files_and_ignores_subdirs(tmp_path: Path):
    """Verify scanning downloads directory captures files with sizes and ages, ignoring directories."""
    f1 = tmp_path / "setup.exe"
    f1.write_bytes(b"dummy_installer" * 50)
    _set_file_age_days(f1, 40.0)

    f2 = tmp_path / "notes.txt"
    f2.write_text("sample content", encoding="utf-8")
    _set_file_age_days(f2, 5.0)

    # Create a subdirectory (e.g. Archive) which should NOT be scanned as an item
    subdir = tmp_path / "Archive"
    subdir.mkdir()
    (subdir / "nested.txt").write_text("nested", encoding="utf-8")

    items, errors = scan_directory(tmp_path)

    assert errors == []
    assert len(items) == 2
    filenames = {i.filename for i in items}
    assert filenames == {"setup.exe", "notes.txt"}

    setup_item = next(i for i in items if i.filename == "setup.exe")
    assert setup_item.size_bytes == len(b"dummy_installer" * 50)
    assert setup_item.age_days >= 39.9


def test_scan_downloads_handles_missing_directory(tmp_path: Path):
    """Verify scanning a non-existent directory reports an error without crashing."""
    missing = tmp_path / "non_existent_folder"
    state = create_initial_state(downloads_dir=missing)

    result = scan_downloads(state)
    assert result["items"] == []
    assert len(result["errors"]) >= 1
    assert "does not exist" in result["errors"][0]


def test_categorization_various_file_types(tmp_path: Path):
    """Verify correct categorization of installers, archives, documents, media, and others."""
    files = {
        "setup.exe": DownloadFileCategory.INSTALLER,
        "package.msi": DownloadFileCategory.INSTALLER,
        "app.pkg": DownloadFileCategory.INSTALLER,
        "tool.deb": DownloadFileCategory.INSTALLER,
        "bundle.zip": DownloadFileCategory.ARCHIVE,
        "data.tar.gz": DownloadFileCategory.ARCHIVE,
        "backup.7z": DownloadFileCategory.ARCHIVE,
        "files.rar": DownloadFileCategory.ARCHIVE,
        "invoice.pdf": DownloadFileCategory.DOCUMENT,
        "sheet.xlsx": DownloadFileCategory.DOCUMENT,
        "doc.docx": DownloadFileCategory.DOCUMENT,
        "data.csv": DownloadFileCategory.DOCUMENT,
        "video.mp4": DownloadFileCategory.MEDIA,
        "song.mp3": DownloadFileCategory.MEDIA,
        "pic.png": DownloadFileCategory.MEDIA,
        "script.py": DownloadFileCategory.OTHER,
    }

    for name, expected_cat in files.items():
        p = tmp_path / name
        p.write_bytes(b"content")
        item = DownloadItem(path=p, filename=name, size_bytes=7, category=DownloadFileCategory.OTHER, age_days=1.0)
        updated = categorize_item(item, installer_max_age_days=30)
        assert updated.category == expected_cat, f"Expected {expected_cat} for {name}, got {updated.category}"


def test_stale_installer_detection_and_action_proposal(tmp_path: Path):
    """Verify installers older than max age are proposed for deletion, while newer are kept."""
    stale_exe = tmp_path / "old_installer.exe"
    stale_exe.write_bytes(b"old")
    stale_item = DownloadItem(path=stale_exe, filename="old_installer.exe", size_bytes=3, category=DownloadFileCategory.OTHER, age_days=45.0)

    recent_exe = tmp_path / "recent_installer.exe"
    recent_exe.write_bytes(b"recent")
    recent_item = DownloadItem(path=recent_exe, filename="recent_installer.exe", size_bytes=6, category=DownloadFileCategory.OTHER, age_days=10.0)

    u_stale = categorize_item(stale_item, installer_max_age_days=30)
    assert u_stale.category == DownloadFileCategory.INSTALLER
    assert u_stale.proposed_action == ProposedAction.DELETE
    assert "45.0 days" in u_stale.rationale or "stale" in u_stale.rationale.lower()

    u_recent = categorize_item(recent_item, installer_max_age_days=30)
    assert u_recent.category == DownloadFileCategory.INSTALLER
    assert u_recent.proposed_action == ProposedAction.KEEP
    assert "recent" in u_recent.rationale.lower() or "limit" in u_recent.rationale.lower()


def test_duplicate_download_detection(tmp_path: Path):
    """Verify files matching duplicate regex pattern are categorized as DUPLICATE and proposed for DELETE."""
    dup_names = [
        "document (1).pdf",
        "statement (2).csv",
        "setup (3).exe",
        "archive (12).zip",
        "my image (1).png",
    ]

    non_dup_names = [
        "document.pdf",
        "statement_1.csv",
        "setup (beta).exe",
        "(1) prefix.zip",
    ]

    for name in dup_names:
        p = tmp_path / name
        p.write_bytes(b"content")
        item = DownloadItem(path=p, filename=name, size_bytes=7, category=DownloadFileCategory.OTHER, age_days=5.0)
        updated = categorize_item(item, installer_max_age_days=30)
        assert updated.category == DownloadFileCategory.DUPLICATE, f"{name} should be categorized as DUPLICATE"
        assert updated.proposed_action == ProposedAction.DELETE
        assert "duplicate" in updated.rationale.lower()

    for name in non_dup_names:
        p = tmp_path / name
        p.write_bytes(b"content")
        item = DownloadItem(path=p, filename=name, size_bytes=7, category=DownloadFileCategory.OTHER, age_days=5.0)
        updated = categorize_item(item, installer_max_age_days=30)
        assert updated.category != DownloadFileCategory.DUPLICATE, f"{name} should not be DUPLICATE"


def test_action_proposal_logic_for_archives_and_documents(tmp_path: Path):
    """Verify documents and archives are proposed for ARCHIVE action."""
    doc = tmp_path / "report.pdf"
    doc.write_bytes(b"pdf")
    arc = tmp_path / "data.zip"
    arc.write_bytes(b"zip")

    item_doc = categorize_item(
        DownloadItem(path=doc, filename="report.pdf", size_bytes=3, category=DownloadFileCategory.OTHER, age_days=10.0),
        installer_max_age_days=30,
    )
    item_arc = categorize_item(
        DownloadItem(path=arc, filename="data.zip", size_bytes=3, category=DownloadFileCategory.OTHER, age_days=10.0),
        installer_max_age_days=30,
    )

    assert item_doc.category == DownloadFileCategory.DOCUMENT
    assert item_doc.proposed_action == ProposedAction.ARCHIVE

    assert item_arc.category == DownloadFileCategory.ARCHIVE
    assert item_arc.proposed_action == ProposedAction.ARCHIVE


def test_assemble_action_plan_groups_categories(tmp_path: Path):
    """Verify assemble_action_plan partitions actionable items by category key and ignores KEEP."""
    items = [
        DownloadItem(path=tmp_path / "old.exe", filename="old.exe", size_bytes=1000, category=DownloadFileCategory.INSTALLER, age_days=40.0, proposed_action=ProposedAction.DELETE),
        DownloadItem(path=tmp_path / "doc (1).pdf", filename="doc (1).pdf", size_bytes=500, category=DownloadFileCategory.DUPLICATE, age_days=2.0, proposed_action=ProposedAction.DELETE),
        DownloadItem(path=tmp_path / "report.pdf", filename="report.pdf", size_bytes=200, category=DownloadFileCategory.DOCUMENT, age_days=10.0, proposed_action=ProposedAction.ARCHIVE),
        DownloadItem(path=tmp_path / "backup.zip", filename="backup.zip", size_bytes=800, category=DownloadFileCategory.ARCHIVE, age_days=15.0, proposed_action=ProposedAction.ARCHIVE),
        DownloadItem(path=tmp_path / "new.exe", filename="new.exe", size_bytes=1000, category=DownloadFileCategory.INSTALLER, age_days=2.0, proposed_action=ProposedAction.KEEP),
        DownloadItem(path=tmp_path / "pic.png", filename="pic.png", size_bytes=300, category=DownloadFileCategory.MEDIA, age_days=5.0, proposed_action=ProposedAction.KEEP),
    ]

    plan = assemble_action_plan(items)
    assert set(plan.keys()) == {
        "delete_installers",
        "delete_duplicates",
        "archive_documents",
        "archive_archives",
    }
    assert len(plan["delete_installers"]) == 1
    assert len(plan["delete_duplicates"]) == 1
    assert len(plan["archive_documents"]) == 1
    assert len(plan["archive_archives"]) == 1

    summaries = format_plan_summary(plan)
    assert len(summaries) == 4
    assert any("stale installer" in s.lower() for s in summaries)


def test_human_review_node_auto_approve_behavior(tmp_path: Path):
    """Verify human_review_node approves all action keys in auto_approve mode."""
    item = DownloadItem(path=tmp_path / "old.exe", filename="old.exe", size_bytes=100, category=DownloadFileCategory.INSTALLER, age_days=50.0, proposed_action=ProposedAction.DELETE)
    state = create_initial_state(downloads_dir=tmp_path, auto_approve=True)
    state["action_plan"] = {"delete_installers": [item]}

    result = human_review_node(state)
    assert result["approved_actions"] == ["delete_installers"]


def test_human_review_node_with_custom_review_func(tmp_path: Path):
    """Verify human_review_node delegates to review_func if provided."""
    i1 = DownloadItem(path=tmp_path / "old.exe", filename="old.exe", size_bytes=100, category=DownloadFileCategory.INSTALLER, age_days=50.0, proposed_action=ProposedAction.DELETE)
    i2 = DownloadItem(path=tmp_path / "doc (1).pdf", filename="doc (1).pdf", size_bytes=50, category=DownloadFileCategory.DUPLICATE, age_days=2.0, proposed_action=ProposedAction.DELETE)
    state = create_initial_state(downloads_dir=tmp_path)
    state["action_plan"] = {"delete_installers": [i1], "delete_duplicates": [i2]}

    # User review function that only approves installers
    def selective_review(plan: dict[str, list[DownloadItem]]) -> list[str]:
        return ["delete_installers"]

    result = human_review_node(state, review_func=selective_review)
    assert result["approved_actions"] == ["delete_installers"]


def test_safety_rule_no_files_touched_without_approval(tmp_path: Path):
    """CRITICAL SAFETY TEST: Verify no files are unlinked or moved if approved_actions is empty."""
    stale_exe = tmp_path / "old_setup.exe"
    stale_exe.write_bytes(b"stale_exe_bytes")

    dup_pdf = tmp_path / "tax (1).pdf"
    dup_pdf.write_bytes(b"dup_pdf_bytes")

    doc_pdf = tmp_path / "contract.docx"
    doc_pdf.write_bytes(b"contract_bytes")

    archive_dir = tmp_path / "Archive"

    items = [
        DownloadItem(path=stale_exe, filename="old_setup.exe", size_bytes=15, category=DownloadFileCategory.INSTALLER, age_days=40.0, proposed_action=ProposedAction.DELETE),
        DownloadItem(path=dup_pdf, filename="tax (1).pdf", size_bytes=13, category=DownloadFileCategory.DUPLICATE, age_days=2.0, proposed_action=ProposedAction.DELETE),
        DownloadItem(path=doc_pdf, filename="contract.docx", size_bytes=14, category=DownloadFileCategory.DOCUMENT, age_days=10.0, proposed_action=ProposedAction.ARCHIVE),
    ]

    freed, moved, errors = execute_approved_actions(
        items=items,
        approved_actions=[],  # Nothing approved!
        archive_dir=archive_dir,
    )

    assert freed == 0
    assert moved == 0
    assert errors == []
    assert stale_exe.exists(), "Unapproved file must remain on disk!"
    assert dup_pdf.exists(), "Unapproved duplicate must remain on disk!"
    assert doc_pdf.exists(), "Unapproved document must remain in downloads!"
    assert not archive_dir.exists(), "Archive directory should not even be created if nothing moved"
    for item in items:
        assert item.is_executed is False


def test_execute_approved_deletions(tmp_path: Path):
    """Verify approved deletions delete files and record freed bytes."""
    stale_exe = tmp_path / "chrome_setup.exe"
    stale_exe.write_bytes(b"x" * 2000)

    dup_pdf = tmp_path / "paper (1).pdf"
    dup_pdf.write_bytes(b"y" * 1000)

    keep_doc = tmp_path / "keep.pdf"
    keep_doc.write_bytes(b"z" * 500)

    items = [
        DownloadItem(path=stale_exe, filename="chrome_setup.exe", size_bytes=2000, category=DownloadFileCategory.INSTALLER, age_days=45.0, proposed_action=ProposedAction.DELETE),
        DownloadItem(path=dup_pdf, filename="paper (1).pdf", size_bytes=1000, category=DownloadFileCategory.DUPLICATE, age_days=3.0, proposed_action=ProposedAction.DELETE),
        DownloadItem(path=keep_doc, filename="keep.pdf", size_bytes=500, category=DownloadFileCategory.DOCUMENT, age_days=1.0, proposed_action=ProposedAction.KEEP),
    ]

    freed, moved, errors = execute_approved_actions(
        items=items,
        approved_actions=["delete_installers", "delete_duplicates"],
        archive_dir=tmp_path / "Archive",
    )

    assert freed == 3000
    assert moved == 0
    assert errors == []
    assert not stale_exe.exists(), "Approved stale installer should be deleted"
    assert not dup_pdf.exists(), "Approved duplicate should be deleted"
    assert keep_doc.exists(), "Unapproved/keep file must remain intact"

    assert items[0].is_executed is True
    assert items[1].is_executed is True
    assert items[2].is_executed is False


def test_execute_approved_archiving(tmp_path: Path):
    """Verify approved archiving moves documents and archives to archive_dir safely."""
    doc = tmp_path / "report.pdf"
    doc.write_bytes(b"report_content")

    archive_zip = tmp_path / "bundle.zip"
    archive_zip.write_bytes(b"zip_content")

    archive_dir = tmp_path / "Archive"

    items = [
        DownloadItem(path=doc, filename="report.pdf", size_bytes=14, category=DownloadFileCategory.DOCUMENT, age_days=20.0, proposed_action=ProposedAction.ARCHIVE),
        DownloadItem(path=archive_zip, filename="bundle.zip", size_bytes=11, category=DownloadFileCategory.ARCHIVE, age_days=15.0, proposed_action=ProposedAction.ARCHIVE),
    ]

    freed, moved, errors = execute_approved_actions(
        items=items,
        approved_actions=["archive_documents", "archive_archives"],
        archive_dir=archive_dir,
    )

    assert freed == 0
    assert moved == 2
    assert errors == []
    assert not doc.exists(), "Original document should be moved"
    assert not archive_zip.exists(), "Original archive should be moved"
    assert (archive_dir / "report.pdf").exists()
    assert (archive_dir / "bundle.zip").exists()
    assert items[0].is_executed is True
    assert items[1].is_executed is True


def test_execute_archiving_handles_name_collisions(tmp_path: Path):
    """Verify archiving avoids overwriting existing files in archive_dir by renaming."""
    archive_dir = tmp_path / "Archive"
    archive_dir.mkdir()
    (archive_dir / "notes.pdf").write_bytes(b"existing_archive_file")

    source_doc = tmp_path / "notes.pdf"
    source_doc.write_bytes(b"new_notes_content")

    items = [
        DownloadItem(path=source_doc, filename="notes.pdf", size_bytes=17, category=DownloadFileCategory.DOCUMENT, age_days=5.0, proposed_action=ProposedAction.ARCHIVE),
    ]

    freed, moved, errors = execute_approved_actions(
        items=items,
        approved_actions=["archive_documents"],
        archive_dir=archive_dir,
    )

    assert moved == 1
    assert (archive_dir / "notes.pdf").read_bytes() == b"existing_archive_file"
    assert (archive_dir / "notes_1.pdf").read_bytes() == b"new_notes_content"


@pytest.mark.asyncio
async def test_end_to_end_graph_execution(tmp_path: Path):
    """Verify entire LangGraph downloads hygiene workflow execution."""
    stale_exe = tmp_path / "old_installer.exe"
    stale_exe.write_bytes(b"old_installer")
    _set_file_age_days(stale_exe, 45.0)

    doc_dup = tmp_path / "report (1).pdf"
    doc_dup.write_bytes(b"dup_content")

    fresh_doc = tmp_path / "statement.pdf"
    fresh_doc.write_bytes(b"statement_content")

    graph = build_downloads_hygiene_graph(auto_approve=True)

    state = create_initial_state(
        downloads_dir=tmp_path,
        archive_dir=tmp_path / "Archive",
        installer_max_age_days=30,
        auto_approve=True,
    )

    final_state = await graph.ainvoke(state)

    assert len(final_state["items"]) == 3
    assert final_state["total_bytes_freed"] > 0
    assert final_state["total_files_moved"] == 1
    assert not stale_exe.exists()
    assert not doc_dup.exists()
    assert not fresh_doc.exists()
    assert (tmp_path / "Archive" / "statement.pdf").exists()


@pytest.mark.asyncio
async def test_run_downloads_hygiene_orchestration(tmp_path: Path):
    """Verify high-level run_downloads_hygiene entrypoint reads Settings and runs graph."""
    stale_exe = tmp_path / "tool_setup.msi"
    stale_exe.write_bytes(b"msi_data")
    _set_file_age_days(stale_exe, 60.0)

    settings = Settings(
        downloads=DownloadsConfig(directory=tmp_path, stale_days=30),
    )

    result_state = await run_downloads_hygiene(settings=settings, auto_approve=True)

    assert result_state["total_bytes_freed"] == len(b"msi_data")
    assert not stale_exe.exists()


def test_no_utf8_bom_in_downloads_agent_files():
    """Verify all Python files in downloads_agent and its test file have no UTF-8 BOM."""
    base_dir = Path(__file__).resolve().parent.parent
    paths_to_check = [
        base_dir / "tests" / "test_downloads_agent.py",
        base_dir / "deskpilot" / "agent_tasks" / "downloads_agent" / "__init__.py",
        base_dir / "deskpilot" / "agent_tasks" / "downloads_agent" / "state.py",
        base_dir / "deskpilot" / "agent_tasks" / "downloads_agent" / "actions.py",
        base_dir / "deskpilot" / "agent_tasks" / "downloads_agent" / "graph.py",
    ]

    for p in paths_to_check:
        if p.exists():
            content = p.read_bytes()
            assert not content.startswith(b"\xef\xbb\xbf"), f"File {p} starts with UTF-8 BOM"


def test_lazy_loading_of_langgraph_and_downloads_agent():
    """Verify boot_tasks and cli imports do not eagerly import langgraph or downloads_agent."""
    import subprocess
    import sys

    code = (
        "import sys; "
        "import deskpilot.cli; "
        "import deskpilot.boot_tasks; "
        "assert 'langgraph' not in sys.modules, 'langgraph eagerly imported!'; "
        "assert 'deskpilot.agent_tasks.downloads_agent' not in sys.modules, 'downloads_agent eagerly imported!'"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, f"Lazy loading check failed: {result.stderr}"
