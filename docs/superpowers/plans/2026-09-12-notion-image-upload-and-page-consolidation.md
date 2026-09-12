# Notion Image Upload & Page Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upload local screenshot image files directly to Notion via `file_uploads` and embed them into pages, while deduplicating and consolidating related screenshots into existing Notion database pages using title overlap and LLM matching.

**Architecture:**
1. Implement `upload_screenshot_to_notion(notion_client, image_path)` in `deskpilot/agent_tasks/screenshot_agent/notion_sync.py` using Notion's `file_uploads.create` and `file_uploads.send` endpoints.
2. In `build_database_page_payload` and `build_page_append_blocks`, append an `image` block referencing the uploaded file ID beneath the text details.
3. Add `fetch_existing_database_pages(notion_client, database_id)` and `match_existing_page(item, existing_pages, llm=None)` to identify similar existing pages (title overlap + LLM verification).
4. Update `sync_screenshot_to_notion` to check for an existing matched page in the target database; if matched, append the screenshot and new details to that existing page (`blocks.children.append`) instead of creating a duplicate page.
5. Thread the selected `llm` from `ScreenshotAgentState` into `notion_sync` so semantic similarity checking reuses the user's active model (Gemini or Ollama).

**Tech Stack:** Python 3.11, Notion Client SDK (`file_uploads`, `data_sources`, `blocks`), LangChain/LangGraph, pytest

## Global Constraints
- Only delete screenshots locally if `is_synced == True` is confirmed.
- If file upload fails, log a warning and continue with text block sync (do not crash the triage run).
- Maintain 100% test pass rate across `uv run pytest`.

---

### Task 1: Add Unit Tests for Notion File Upload and Image Block Creation

**Files:**
- Modify: `tests/test_screenshot_agent.py`

**Interfaces:**
- Produces: Tests covering:
  - `upload_screenshot_to_notion` succeeds with PNG/JPEG and returns file ID.
  - `upload_screenshot_to_notion` handles missing file or upload error gracefully returning `None`.
  - `build_database_page_payload` includes an `image` block when `file_upload_id` is provided.
  - `build_page_append_blocks` includes an `image` block when `file_upload_id` is provided.

- [ ] **Step 1: Write failing tests in `tests/test_screenshot_agent.py`**

```python
def test_upload_screenshot_to_notion_success(tmp_path: Path):
    """Verify upload_screenshot_to_notion creates and sends file via notion_client."""
    from unittest.mock import MagicMock
    from deskpilot.agent_tasks.screenshot_agent.notion_sync import upload_screenshot_to_notion

    img_file = tmp_path / "test.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\nfakecontent")

    mock_client = MagicMock()
    mock_client.file_uploads.create.return_value = {"id": "fu_123"}
    mock_client.file_uploads.send.return_value = {"id": "fu_123", "status": "uploaded"}

    file_id = upload_screenshot_to_notion(mock_client, img_file)
    assert file_id == "fu_123"
    mock_client.file_uploads.create.assert_called_once_with(filename="test.png", content_type="image/png")
    mock_client.file_uploads.send.assert_called_once()


def test_upload_screenshot_to_notion_error_returns_none(tmp_path: Path):
    """Verify upload_screenshot_to_notion returns None on error without raising."""
    from unittest.mock import MagicMock
    from deskpilot.agent_tasks.screenshot_agent.notion_sync import upload_screenshot_to_notion

    img_file = tmp_path / "missing.png"
    mock_client = MagicMock()
    file_id = upload_screenshot_to_notion(mock_client, img_file)
    assert file_id is None


def test_build_database_page_payload_with_image(tmp_path: Path):
    """Verify image block is included in children blocks below details paragraph."""
    from deskpilot.agent_tasks.screenshot_agent.notion_sync import build_database_page_payload
    from deskpilot.agent_tasks.screenshot_agent.state import ScreenshotItem

    item = ScreenshotItem(path=tmp_path / "shot.png", title="Test Title", cluster_tag="Work")
    payload = build_database_page_payload(item, database_id="db_1", schema={}, file_upload_id="fu_abc")

    children = payload.get("children", [])
    assert len(children) == 3
    assert children[0]["type"] == "heading_2"
    assert children[1]["type"] == "paragraph"
    assert children[2]["type"] == "image"
    assert children[2]["image"]["file_upload"]["id"] == "fu_abc"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_screenshot_agent.py -k "upload_screenshot_to_notion or with_image" -v`
Expected: FAIL (ImportError / unexpected keyword argument)

- [ ] **Step 3: Implement `upload_screenshot_to_notion` and update payload builders in `notion_sync.py`**

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_screenshot_agent.py -k "upload_screenshot_to_notion or with_image" -v`
Expected: PASS

- [ ] **Step 5: Commit Task 1**

```bash
git add deskpilot/agent_tasks/screenshot_agent/notion_sync.py tests/test_screenshot_agent.py
git commit -m "feat(notion): add screenshot file upload and image block embedding"
```

---

### Task 2: Add Unit Tests and Logic for Page Consolidation and Similarity Matching

**Files:**
- Modify: `tests/test_screenshot_agent.py`
- Modify: `deskpilot/agent_tasks/screenshot_agent/notion_sync.py`

**Interfaces:**
- Produces:
  - `fetch_existing_database_pages(notion_client, database_id: str) -> list[dict[str, str]]`
    Returns list of `{"id": page_id, "title": page_title}`.
  - `match_existing_page(item: ScreenshotItem, existing_pages: list[dict[str, str]], llm: Any = None) -> str | None`
    Returns matched `page_id` or `None`.
  - `build_page_append_section_blocks(item: ScreenshotItem, file_upload_id: str | None = None) -> list[dict[str, Any]]`
    Returns divider, sub-heading, details, and optional image block for appending to an existing page.

- [ ] **Step 1: Write failing tests for similarity matching and section block generation**

```python
def test_match_existing_page_exact_and_token_overlap(tmp_path: Path):
    """Verify token overlap finds existing page without calling LLM."""
    from deskpilot.agent_tasks.screenshot_agent.notion_sync import match_existing_page
    from deskpilot.agent_tasks.screenshot_agent.state import ScreenshotItem

    pages = [
        {"id": "p1", "title": "Microsoft Copilot Studio Notes"},
        {"id": "p2", "title": "Job Prospect of an AI Engineer"},
    ]

    item1 = ScreenshotItem(path=tmp_path / "a.png", title="Job Prospect of an AI Engineer")
    assert match_existing_page(item1, pages) == "p2"

    item2 = ScreenshotItem(path=tmp_path / "b.png", title="AI Engineer Job Prospects")
    assert match_existing_page(item2, pages) == "p2"

    item3 = ScreenshotItem(path=tmp_path / "c.png", title="Unrelated Cooking Recipe")
    assert match_existing_page(item3, pages) is None


def test_match_existing_page_llm_verification(tmp_path: Path):
    """Verify LLM is queried when token overlap is ambiguous."""
    from unittest.mock import MagicMock
    from deskpilot.agent_tasks.screenshot_agent.notion_sync import match_existing_page
    from deskpilot.agent_tasks.screenshot_agent.state import ScreenshotItem

    pages = [{"id": "p1", "title": "AI Engineering Careers & Outlook"}]
    item = ScreenshotItem(path=tmp_path / "d.png", title="Tech Salary 2026", rationale="AI engineer comp")

    mock_llm = MagicMock()
    mock_llm.invoke.return_value.content = "AI Engineering Careers & Outlook"

    assert match_existing_page(item, pages, llm=mock_llm) == "p1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_screenshot_agent.py -k "test_match_existing_page" -v`
Expected: FAIL

- [ ] **Step 3: Implement `fetch_existing_database_pages`, `match_existing_page`, and `build_page_append_section_blocks`**

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_screenshot_agent.py -k "test_match_existing_page" -v`
Expected: PASS

- [ ] **Step 5: Commit Task 2**

```bash
git add deskpilot/agent_tasks/screenshot_agent/notion_sync.py tests/test_screenshot_agent.py
git commit -m "feat(notion): implement existing page matching and consolidation section builder"
```

---

### Task 3: Integrate Consolidation & Image Upload into `sync_screenshot_to_notion` and Graph

**Files:**
- Modify: `deskpilot/agent_tasks/screenshot_agent/notion_sync.py`
- Modify: `deskpilot/agent_tasks/screenshot_agent/graph.py`
- Modify: `tests/test_screenshot_agent.py`

**Interfaces:**
- Updates `sync_screenshot_to_notion(..., llm=None, consolidate_pages=True)` to:
  1. Upload screenshot file.
  2. If target is a database, check `match_existing_page`.
  3. If matched, append consolidation section (`blocks.children.append`) to that page.
  4. If not matched, create new page with image block.
- Updates `notion_sync` node in `graph.py` to pass `llm` from state.

- [ ] **Step 1: Add integration test verifying sync consolidates into existing page instead of creating new page**

- [ ] **Step 2: Update `sync_screenshot_to_notion` and `sync_approved_items` to upload images and consolidate**

- [ ] **Step 3: Update `graph.py` to pass `llm` into `notion_sync` node**

- [ ] **Step 4: Run test suite to verify all pass**

Run: `uv run pytest tests/test_screenshot_agent.py tests/test_cli.py`
Expected: 100% PASS

- [ ] **Step 5: Commit Task 3**

```bash
git add deskpilot/agent_tasks/screenshot_agent/ tests/
git commit -m "feat(notion): integrate screenshot image upload and page consolidation into sync workflow"
```

---

### Task 4: Full Suite Verification & Live Verification
- Run complete test suite: `uv run pytest`
- Verify against real Notion database with clean error handling.
