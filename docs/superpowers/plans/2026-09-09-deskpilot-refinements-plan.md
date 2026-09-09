# DeskPilot Refinements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved DeskPilot refinements: decouple Floorp bookmarks from boot to on-demand only, add winget package exclusions (`AdvancedSystemCare`, `RevoUninstallerPro`), build Notion schema introspection for `"WorkNote"`, and enable multi-destination screenshot routing.

**Architecture:** Maintain the sub-second hybrid execution model. Boot tasks stay fast and zero-LLM. Multi-destination screenshot triage routes images dynamically to Notion databases/pages or local keep, while Floorp bookmark audits run only when chosen from the menu.

**Tech Stack:** Python 3.11+, `uv`, `rich`, `pydantic`, `langgraph`, `notion-client`, `pytest`, `pytest-asyncio`.

## Global Constraints

- Use `uv` exclusively for running and test execution (`uv run pytest ...`).
- Fast boot tasks MUST NOT import heavy LLM or LangGraph libraries.
- Never delete unconfirmed or unsynced files.
- Strict adherence to TDD: failing test -> confirm red -> implement minimal code -> verify green.

---

### Task 1: Decouple Floorp Bookmarks from Phase 1 Boot

**Files:**
- Modify: `deskpilot/cli.py`
- Modify: `deskpilot/state.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- `run_phase1_boot_sequence(settings: Settings) -> BootState`: Executes only `clean_temp_directory`, `check_winget_updates`, and `fetch_today_agenda`.
- `render_dashboard(state: BootState, console: Console | None = None)`: Renders 3-panel dashboard (System Temp, Winget Updates, Calendar Agenda).
- Menu option `[3]` remains on-demand `Clean Floorp Bookmarks`.

- [ ] **Step 1: Write failing tests in `tests/test_cli.py`**
Verify `run_phase1_boot_sequence` does NOT invoke `audit_floorp_bookmarks` on boot, `state.bookmarks` remains None until option `[3]` is selected, and dashboard renders 3 primary panels cleanly.

- [ ] **Step 2: Run test to verify failure**
Run: `uv run pytest tests/test_cli.py -k "boot_sequence" -v`
Expected: FAIL.

- [ ] **Step 3: Update `deskpilot/cli.py`**
Remove `audit_floorp_bookmarks` from `asyncio.gather` in `run_phase1_boot_sequence`. Adjust layout in `render_dashboard`. Keep option `[3]` in `execute_menu_action` running `audit_floorp_bookmarks`.

- [ ] **Step 4: Run test to verify it passes**
Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add deskpilot/cli.py deskpilot/state.py tests/test_cli.py
git commit -m "refactor(cli): decouple Floorp bookmark audit from boot to on-demand"
```

---

### Task 2: Winget Package Exclusions (`AdvancedSystemCare` & `RevoUninstallerPro`)

**Files:**
- Modify: `deskpilot/config.py`
- Modify: `config.yaml`
- Modify: `deskpilot/boot_tasks/package_checker.py`
- Modify: `deskpilot/cli.py`
- Test: `tests/test_package_checker.py`
- Test: `tests/test_config.py`

**Interfaces:**
- `WingetConfig.ignore_packages: list[str] = ["AdvancedSystemCare", "RevoUninstallerPro"]`
- `parse_winget_output(raw_output: str, ignore_packages: list[str] | None = None) -> list[WingetUpdateItem]`
- Substring match against package `name` and `id` (case-insensitive).

- [ ] **Step 1: Write failing tests in `tests/test_package_checker.py` and `tests/test_config.py`**
Test `ignore_packages` default values in `WingetConfig`, and verify `parse_winget_output` filters out rows matching `AdvancedSystemCare` and `RevoUninstallerPro`.

- [ ] **Step 2: Run test to verify failure**
Run: `uv run pytest tests/test_package_checker.py -k "ignore" -v`
Expected: FAIL.

- [ ] **Step 3: Implement `ignore_packages` in `deskpilot/config.py` and `package_checker.py`**
Add field to `WingetConfig`. In `parse_winget_output`, skip items matching any pattern in `ignore_packages`. In `deskpilot/cli.py` option `[4]`, pass ignore arguments or inform user if batch upgrading.

- [ ] **Step 4: Run test to verify it passes**
Run: `uv run pytest tests/test_package_checker.py tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add deskpilot/config.py config.yaml deskpilot/boot_tasks/package_checker.py deskpilot/cli.py tests/
git commit -m "feat(packages): add winget package exclusions for AdvancedSystemCare and RevoUninstallerPro"
```

---

### Task 3: Notion Schema Introspection & Multi-Destination Screenshot Routing

**Files:**
- Modify: `deskpilot/config.py`
- Modify: `config.yaml`
- Modify: `deskpilot/agent_tasks/screenshot_agent/state.py`
- Modify: `deskpilot/agent_tasks/screenshot_agent/vision.py`
- Modify: `deskpilot/agent_tasks/screenshot_agent/notion_sync.py`
- Modify: `deskpilot/agent_tasks/screenshot_agent/graph.py`
- Test: `tests/test_screenshot_agent.py`

**Interfaces:**
- `ScreenshotDestinationsConfig`:
  - `work_notes_database_id: str = ""` (for `"WorkNote"`)
  - `due_diligence_page_id: str = ""` (for `"Due Diligence Questionnaire"`)
  - `brainstorm_page_id: str = ""` (for `"Brainstorm Session"`)
- Classification categories: `WORK_NOTES`, `DUE_DILIGENCE`, `BRAINSTORM`, `LOCAL_KEEP`.
- `sync_to_notion_destination(client, item, destinations_config) -> bool`:
  - If target is database (`work_notes_database_id`): call `client.databases.retrieve`, introspect schema, map title/tags/date properties dynamically, create page.
  - If target is page (`due_diligence_page_id`, `brainstorm_page_id`): append note content as child blocks / sub-page.
  - If `LOCAL_KEEP`: skip sync, `is_synced=False`, preserve file.

- [ ] **Step 1: Write failing tests in `tests/test_screenshot_agent.py`**
Test multi-destination classification, Notion database schema introspection (`client.databases.retrieve`), dynamic property matching (Title, Tags, Date), and page block appending.

- [ ] **Step 2: Run test to verify failure**
Run: `uv run pytest tests/test_screenshot_agent.py -k "destination or introspect" -v`
Expected: FAIL.

- [ ] **Step 3: Implement multi-destination models & introspection in screenshot agent**
Update `deskpilot/config.py` with `ScreenshotDestinationsConfig`. Update `vision.py` classification prompt. Implement `introspect_and_map_database_properties()` in `notion_sync.py`. Wire into `graph.py`.

- [ ] **Step 4: Run test to verify it passes**
Run: `uv run pytest tests/test_screenshot_agent.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add deskpilot/ config.yaml tests/test_screenshot_agent.py
git commit -m "feat(agent): Notion schema introspection and multi-destination screenshot routing"
```

---

### Task 4: Full System Verification & Updated Documentation

**Files:**
- Modify: `README.md`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Update README.md with new configuration & destination options**
Document `ignore_packages`, `screenshot_destinations` (`work_notes_database_id`, `due_diligence_page_id`, `brainstorm_page_id`), and on-demand Floorp usage.

- [ ] **Step 2: Run complete regression suite**
Run: `uv run pytest -v`
Expected: ALL TESTS PASS (100% green).

- [ ] **Step 3: Commit**
```bash
git add README.md tests/
git commit -m "docs: update README with winget exclusions, destination routing, and on-demand bookmarks"
```
