# Notion Sync Error Handling & Interactive Key Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement error detection and interactive user prompt recovery for Notion token and database ID failures in the screenshot agent, gracefully completing the task without passing to Notion when Enter is pressed with empty input.

**Architecture:**
1. Update `sync_screenshot_to_notion` in `deskpilot/agent_tasks/screenshot_agent/notion_sync.py` to return detailed status/error information instead of silently swallowing exceptions.
2. Update `sync_approved_items` in `notion_sync.py` to accept an optional `prompt_func: Callable[[str], str] | None` and `console_print: Callable[[str], None] | None` to interactively prompt for corrected keys/database IDs on failure or abort cleanly when Enter is pressed.
3. Thread `prompt_func` through `ScreenshotAgentState`, `notion_sync`, `build_screenshot_triage_graph`, and `run_screenshot_triage` in `deskpilot/agent_tasks/screenshot_agent/graph.py`.
4. In `deskpilot/cli.py`, pass the interactive `prompt_func` to `run_screenshot_triage`, and when Notion sync is skipped/unsuccessful, output: `"Finished the task without passing to Notion."`
5. Add unit tests in `tests/test_screenshot_agent.py` covering token retry, database ID retry, and graceful empty-input exit.

**Tech Stack:** Python 3.11, Notion Client SDK, LangGraph, pytest

## Global Constraints
- Do not delete screenshots locally if Notion sync fails or is skipped (`is_synced == False`).
- If user submits empty input (Enter), gracefully finish the task and display `"Finished the task without passing to Notion."`
- Preserve 100% test pass rate on `uv run pytest`.

---

### Task 1: Add Unit Tests for Notion Error Handling and Interactive Key Recovery

**Files:**
- Modify: `tests/test_screenshot_agent.py`

**Interfaces:**
- Produces: Tests covering:
  - 401 Unauthorized token recovery via `prompt_func`
  - 404 Database/Page ID recovery via `prompt_func`
  - Empty input (Enter) finishing task cleanly without Notion sync

- [ ] **Step 1: Write the failing tests in `tests/test_screenshot_agent.py`**

```python
def test_sync_approved_items_interactive_token_recovery(tmp_path: Path):
    """Verify 401 unauthorized token error prompts user for new token and retries sync."""
    from unittest.mock import MagicMock
    from deskpilot.agent_tasks.screenshot_agent.notion_sync import sync_approved_items
    from deskpilot.agent_tasks.screenshot_agent.state import ScreenshotItem
    from deskpilot.config import ScreenshotDestinationsConfig

    item = ScreenshotItem(path=tmp_path / "a.png", classification="WORK_NOTES", cluster_tag="Dev")
    mock_client = MagicMock()
    # First call raises 401 unauthorized, second succeeds
    mock_client.pages.create.side_effect = [Exception("unauthorized 401 invalid token"), {"id": "page_ok"}]
    mock_client.databases.retrieve.return_value = {"id": "db_1", "properties": {"Title": {"type": "title"}}}

    prompts = []
    def fake_prompt(msg: str) -> str:
        prompts.append(msg)
        return "secret_new_valid_token"

    synced, errors = sync_approved_items(
        items=[item],
        approved_cluster_keys=["Dev"],
        notion_client=mock_client,
        database_id="db_1",
        prompt_func=fake_prompt,
    )
    assert len(prompts) == 1
    assert "token" in prompts[0].lower()


def test_sync_approved_items_interactive_db_id_recovery(tmp_path: Path):
    """Verify 404 object_not_found database error prompts user for new DB ID and retries."""
    from unittest.mock import MagicMock
    from deskpilot.agent_tasks.screenshot_agent.notion_sync import sync_approved_items
    from deskpilot.agent_tasks.screenshot_agent.state import ScreenshotItem

    item = ScreenshotItem(path=tmp_path / "b.png", classification="WORK_NOTES", cluster_tag="Dev")
    mock_client = MagicMock()
    # 404 on bad database
    mock_client.databases.retrieve.side_effect = [Exception("object_not_found 404"), {"id": "db_corrected", "properties": {"Title": {"type": "title"}}}]
    mock_client.pages.create.return_value = {"id": "page_ok"}

    prompts = []
    def fake_prompt(msg: str) -> str:
        prompts.append(msg)
        return "db_corrected"

    synced, errors = sync_approved_items(
        items=[item],
        approved_cluster_keys=["Dev"],
        notion_client=mock_client,
        database_id="db_bad",
        prompt_func=fake_prompt,
    )
    assert len(prompts) == 1
    assert ("database" in prompts[0].lower() or "id" in prompts[0].lower() or "key" in prompts[0].lower())


def test_sync_approved_items_empty_prompt_finishes_without_notion(tmp_path: Path):
    """Verify pressing Enter (empty prompt) skips Notion sync gracefully without failing local triage."""
    from unittest.mock import MagicMock
    from deskpilot.agent_tasks.screenshot_agent.notion_sync import sync_approved_items
    from deskpilot.agent_tasks.screenshot_agent.state import ScreenshotItem

    item = ScreenshotItem(path=tmp_path / "c.png", classification="WORK_NOTES", cluster_tag="Dev")
    mock_client = MagicMock()
    mock_client.databases.retrieve.side_effect = Exception("object_not_found 404")

    synced, errors = sync_approved_items(
        items=[item],
        approved_cluster_keys=["Dev"],
        notion_client=mock_client,
        database_id="db_bad",
        prompt_func=lambda _: "",  # User pressed Enter with empty key
    )
    assert synced == 0
    assert item.is_synced is False
    assert any("without passing to Notion" in err or "skipped" in err.lower() or "not found" in err.lower() for err in errors)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_screenshot_agent.py -k "test_sync_approved_items_interactive" -v`
Expected: FAIL (`unexpected keyword argument 'prompt_func'`)

---

### Task 2: Implement Interactive Error Handling and Key Recovery in `notion_sync.py`

**Files:**
- Modify: `deskpilot/agent_tasks/screenshot_agent/notion_sync.py`

**Interfaces:**
- Updates `sync_screenshot_to_notion` to return `tuple[bool, Exception | None]` or raise structured errors.
- Updates `sync_approved_items(..., prompt_func=None, console_print=None)`.

- [ ] **Step 1: Update `sync_screenshot_to_notion` and `sync_approved_items`**
  - Detect error type (`unauthorized`/`token` vs `not_found`/`database_id`).
  - If error occurs and `prompt_func` is supplied:
    - Display clear error notice.
    - Prompt for corrected key or target ID.
    - If user enters corrected value: apply and retry.
    - If user enters empty string (Enter): record `"Finished the task without passing to Notion."`, mark `is_synced = False`, and return safely.

- [ ] **Step 2: Run unit tests to verify they pass**

Run: `uv run pytest tests/test_screenshot_agent.py -k "test_sync_approved_items_interactive" -v`
Expected: PASS

- [ ] **Step 3: Commit Task 2**

```bash
git add deskpilot/agent_tasks/screenshot_agent/notion_sync.py tests/test_screenshot_agent.py
git commit -m "feat(notion): add interactive key and database recovery for screenshot sync failures"
```

---

### Task 3: Integrate with `graph.py` and `cli.py`

**Files:**
- Modify: `deskpilot/agent_tasks/screenshot_agent/state.py`
- Modify: `deskpilot/agent_tasks/screenshot_agent/graph.py`
- Modify: `deskpilot/cli.py`

- [ ] **Step 1: Pass `prompt_func` through state and graph runner**
  - Allow `ScreenshotAgentState` to optionally carry `prompt_func`.
  - In `cli.py`, pass `prompt_func=prompt_func` to `run_screenshot_triage`.
  - In `cli.py`, if `synced == 0` and there were syncable approved items or if errors indicate skipped Notion sync, print:
    `"[yellow]Finished the task without passing to Notion.[/yellow]"`

- [ ] **Step 2: Run full test suite**

Run: `uv run pytest`
Expected: 100% PASS

- [ ] **Step 3: Commit Task 3**

```bash
git add deskpilot/agent_tasks/screenshot_agent/ deskpilot/cli.py
git commit -m "feat(cli): wire interactive key recovery prompt into screenshot triage execution"
```

---

### Task 4: Final Verification
- Run complete test suite: `uv run pytest`
