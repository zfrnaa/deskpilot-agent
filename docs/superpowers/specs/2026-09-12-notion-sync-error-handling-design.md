# Notion Sync Error Handling and Interactive Key Recovery Spec

## Context
During screenshot triage, when syncing approved items to Notion, failures currently report a generic:
`Failed to sync Screenshot 2026-07-04 140646.png to Notion target <id>`
without distinguishing whether the Notion API token is unauthorized (`401`) or the database/page ID is incorrect or inaccessible (`404` / `object_not_found`).
If the user forgot to configure or update the key or database ID, the process finishes without offering a chance to correct it or gracefully skip Notion without erroring out.

## Goals
1. Provide granular error detection (token unauthorized vs target ID not found/inaccessible).
2. Implement interactive error recovery prompting the user for the corrected token or database ID.
3. If the user presses Enter with empty input, finish the task cleanly with:
   `"Finished the task without passing to Notion"` (safely preserving local files).
4. If the user inputs a corrected token/ID, dynamically retry the sync.

---

## Architecture & Design

### 1. Granular Error Diagnostics (`notion_sync.py`)
Modify `sync_screenshot_to_notion` and `sync_approved_items`:
- Capture Notion client exception details:
  - Check error code / message:
    - Token issue: `401`, `unauthorized`, `restricted_service`, `invalid_token`
    - Target ID issue: `404`, `object_not_found`, `could_not_find_database`, `could_not_find_page`, `validation_error`
- Pass an optional `prompt_func: Callable[[str], str] | None` down to `sync_approved_items` (or handle through `graph.py` / `cli.py`).

### 2. Interactive Prompt & Recovery Handler
When a failure occurs:
- If `unauthorized`:
  - Show message: `Failed to send to Notion: Invalid or unauthorized Notion API token.`
  - Prompt: `Enter correct Notion API token (or press Enter to finish without passing to Notion): `
  - If token provided: Re-initialize `notion_client = Client(auth=new_token)` and retry.
  - If empty: Log warning and abort remaining Notion syncs gracefully.
- If `object_not_found` (database/page ID):
  - Show message: `Failed to send {item.filename} to Notion target {target_id}. Check if the database/page ID is correct.`
  - Prompt: `Enter correct Notion database/page ID (or press Enter to finish without passing to Notion): `
  - If ID provided: Update target destination and retry.
  - If empty: Skip Notion sync for this item (or remaining items for this destination) and continue.

### 3. Graceful Task Finish
- In `cli.py`, if any items failed to sync due to skipped/aborted Notion sync:
  - Display informative message: `"Finished the task without passing to Notion."`
  - Ensure `cleanup_synced` does not delete the un-synced screenshots locally.

---

## Verification Plan
1. Unit tests in `tests/test_screenshot_agent.py`:
   - Test prompt callback upon 401 error with new token retry.
   - Test prompt callback upon 404 error with new database ID retry.
   - Test prompt callback with empty response (Enter pressed) -> finishes task gracefully without passing to Notion.
2. Full test suite execution: `uv run pytest` to ensure no regressions in boot sequence or CLI workflows.
