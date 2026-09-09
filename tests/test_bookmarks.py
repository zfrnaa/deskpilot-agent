from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from deskpilot.boot_tasks.bookmarks import (
    BookmarkAuditResult,
    audit_floorp_bookmarks,
    find_floorp_profile,
    is_title_noisy,
    normalize_url,
)
from deskpilot.config import FloorpConfig


def _create_mock_places_db(db_path: Path) -> Path:
    """Create a mock SQLite places.sqlite with moz_places and moz_bookmarks tables."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE moz_places (
            id INTEGER PRIMARY KEY,
            url TEXT,
            title TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE moz_bookmarks (
            id INTEGER PRIMARY KEY,
            type INTEGER,
            fk INTEGER,
            parent INTEGER,
            position INTEGER,
            title TEXT,
            dateAdded INTEGER
        )
        """
    )

    # Insert test data:
    # 1. Clean bookmark
    cur.execute("INSERT INTO moz_places (id, url, title) VALUES (1, 'https://github.com/astral-sh/uv', 'Astral UV')")
    cur.execute("INSERT INTO moz_bookmarks (id, type, fk, parent, position, title, dateAdded) VALUES (101, 1, 1, 1, 0, 'Astral UV Repo', 1600000000)")

    # 2. Noisy title (empty title)
    cur.execute("INSERT INTO moz_places (id, url, title) VALUES (2, 'https://docs.astral.sh/uv/', 'Overview')")
    cur.execute("INSERT INTO moz_bookmarks (id, type, fk, parent, position, title, dateAdded) VALUES (102, 1, 2, 1, 1, '', 1600000001)")

    # 3. Noisy title (URL as title)
    cur.execute("INSERT INTO moz_places (id, url, title) VALUES (3, 'https://news.ycombinator.com', 'Hacker News')")
    cur.execute("INSERT INTO moz_bookmarks (id, type, fk, parent, position, title, dateAdded) VALUES (103, 1, 3, 1, 2, 'https://news.ycombinator.com', 1600000002)")

    # 4. Duplicate group URL 1 (clean title, same normalized url as #5)
    cur.execute("INSERT INTO moz_places (id, url, title) VALUES (4, 'https://python.org/?utm_source=twitter', 'Python Home')")
    cur.execute("INSERT INTO moz_bookmarks (id, type, fk, parent, position, title, dateAdded) VALUES (104, 1, 4, 1, 3, 'Python Official', 1600000003)")

    # 5. Duplicate group URL 2 (clean title, same normalized url as #4 with trailing slash and no query)
    cur.execute("INSERT INTO moz_places (id, url, title) VALUES (5, 'https://www.python.org/', 'Python Org')")
    cur.execute("INSERT INTO moz_bookmarks (id, type, fk, parent, position, title, dateAdded) VALUES (105, 1, 5, 1, 4, 'Welcome to Python', 1600000004)")

    # 6. Folder header or separator (type != 1) - must be ignored
    cur.execute("INSERT INTO moz_bookmarks (id, type, fk, parent, position, title, dateAdded) VALUES (106, 2, NULL, 1, 5, 'Bookmarks Bar', 1600000005)")

    # 7. Special 'place:' query URL - must be ignored
    cur.execute("INSERT INTO moz_places (id, url, title) VALUES (7, 'place:folder=BOOKMARKS_MENU', 'Recent Bookmarks')")
    cur.execute("INSERT INTO moz_bookmarks (id, type, fk, parent, position, title, dateAdded) VALUES (107, 1, 7, 1, 6, 'Recent', 1600000006)")

    conn.commit()
    conn.close()
    return db_path


def test_bookmark_audit_result_model():
    """Verify BookmarkAuditResult model instantiation and default values."""
    res_default = BookmarkAuditResult()
    assert res_default.total_bookmarks == 0
    assert res_default.noisy_count == 0
    assert res_default.duplicate_groups_count == 0
    assert res_default.sample_noisy == []
    assert res_default.sample_duplicates == []
    assert res_default.profile_path is None
    assert res_default.error is None

    res_custom = BookmarkAuditResult(
        total_bookmarks=42,
        noisy_count=5,
        duplicate_groups_count=2,
        sample_noisy=[{"id": 1, "title": ""}],
        sample_duplicates=[{"url": "example.com", "count": 2}],
        profile_path=Path("/test/path"),
        error=None,
    )
    assert res_custom.total_bookmarks == 42
    assert res_custom.noisy_count == 5
    assert res_custom.duplicate_groups_count == 2
    assert len(res_custom.sample_noisy) == 1
    assert len(res_custom.sample_duplicates) == 1


def test_noisy_title_heuristics():
    """Verify detection of empty, whitespace, and URL-like titles."""
    assert is_title_noisy(None, "https://example.com")
    assert is_title_noisy("", "https://example.com")
    assert is_title_noisy("   ", "https://example.com")
    assert is_title_noisy("https://example.com", "https://example.com")
    assert is_title_noisy("https://example.com/", "https://example.com")
    assert is_title_noisy("http://example.com", "https://example.com")
    assert is_title_noisy("example.com", "https://example.com")
    assert is_title_noisy("www.example.com", "https://example.com")
    assert is_title_noisy("github.com/foo/bar", "https://github.com/foo/bar")

    # Clean titles should NOT be noisy
    assert not is_title_noisy("Python Documentation", "https://docs.python.org/3/")
    assert not is_title_noisy("DeskPilot Orchestrator", "https://github.com/user/deskpilot")


def test_normalize_url_and_duplicates():
    """Verify URL normalization strips tracking parameters, schemes, www, and trailing slashes."""
    u1 = normalize_url("https://www.example.com/path/?utm_source=twitter&utm_medium=social")
    u2 = normalize_url("http://example.com/path")
    assert u1 == "example.com/path"
    assert u2 == "example.com/path"
    assert u1 == u2


@pytest.mark.asyncio
async def test_audit_floorp_bookmarks_success(tmp_path: Path):
    """Verify bookmark audit detects noisy titles and duplicate URL groups on mock places.sqlite."""
    db_file = tmp_path / "places.sqlite"
    _create_mock_places_db(db_file)

    result = await audit_floorp_bookmarks(profile_path=tmp_path)

    assert result.error is None
    # 5 active bookmarks (ignoring folder #106 and place: #107)
    assert result.total_bookmarks == 5
    # 2 noisy: #102 (empty title), #103 (url as title)
    assert result.noisy_count == 2
    # 1 duplicate group: #104 and #105 (python.org)
    assert result.duplicate_groups_count == 1
    assert len(result.sample_noisy) == 2
    assert len(result.sample_duplicates) == 1
    assert result.profile_path == tmp_path.resolve()


@pytest.mark.asyncio
async def test_read_only_connection_prevents_writes(tmp_path: Path):
    """Verify that opening places.sqlite in read-only mode disallows modifications."""
    db_file = tmp_path / "places.sqlite"
    _create_mock_places_db(db_file)

    # Perform audit
    result = await audit_floorp_bookmarks(profile_path=db_file)
    assert result.error is None

    # Connect using read-only URI and assert attempts to write raise an error
    ro_uri = f"{db_file.resolve().as_uri()}?immutable=1&mode=ro"
    ro_conn = sqlite3.connect(ro_uri, uri=True)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        ro_conn.execute("DELETE FROM moz_bookmarks")
    ro_conn.close()


@pytest.mark.asyncio
async def test_missing_profile_or_database_handled_gracefully(tmp_path: Path):
    """Verify non-existent profile path returns a structured error without crashing."""
    missing_dir = tmp_path / "non_existent_profile"
    result = await audit_floorp_bookmarks(profile_path=missing_dir)

    assert result.total_bookmarks == 0
    assert result.error is not None
    assert "not found" in result.error.lower()


@pytest.mark.asyncio
async def test_audit_with_config_disabled(tmp_path: Path):
    """Verify audit exits early when disabled via FloorpConfig."""
    db_file = tmp_path / "places.sqlite"
    _create_mock_places_db(db_file)

    cfg = FloorpConfig(enabled=False, profile_path=tmp_path)
    result = await audit_floorp_bookmarks(config=cfg)

    assert result.total_bookmarks == 0
    assert result.noisy_count == 0
    assert result.duplicate_groups_count == 0
    assert result.error is None


def test_find_floorp_profile_default_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify find_floorp_profile automatically discovers *.default-release subdirectory."""
    floorp_profiles = tmp_path / "Floorp" / "Profiles"
    target_profile = floorp_profiles / "abc12345.default-release"
    target_profile.mkdir(parents=True)
    (target_profile / "places.sqlite").touch()

    monkeypatch.setenv("APPDATA", str(tmp_path))

    discovered = find_floorp_profile()
    assert discovered is not None
    assert discovered.resolve() == target_profile.resolve()


def test_no_utf8_bom_in_bookmark_files():
    """Verify no UTF-8 BOM exists in bookmark audit source and test files."""
    project_root = Path(__file__).parent.parent
    py_files = [
        project_root / "deskpilot" / "boot_tasks" / "bookmarks.py",
        project_root / "tests" / "test_bookmarks.py",
    ]
    for py_file in py_files:
        if py_file.exists():
            raw_bytes = py_file.read_bytes()
            assert not raw_bytes.startswith(b"\xef\xbb\xbf"), f"BOM found in {py_file}"
