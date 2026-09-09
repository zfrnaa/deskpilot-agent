# DeskPilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build **DeskPilot**, an intelligent, open-source personal boot orchestrator that executes fast parallel morning hygiene checks (Windows %TEMP% cleanup, winget package audit, Floorp bookmark check, Google Calendar agenda) and provides an interactive Rich terminal dashboard with on-demand LangGraph agents (Screenshot Triage to Notion, Downloads hygiene, Notion Read-Later digest).

**Architecture:** Hybrid execution model. Fast sub-second tasks run concurrently via `asyncio` without LLM overhead. Intelligent tasks run as on-demand LangGraph agents with full LangSmith tracing. Packaging and virtual environments managed strictly via `uv`.

**Tech Stack:** Python 3.11+, `uv`, `rich`, `pydantic`, `pydantic-settings`, `google-api-python-client`, `google-auth-oauthlib`, `langgraph`, `langchain-core`, `langchain-google-genai` (or OpenAI-compatible), `notion-client`, `pytest`, `pytest-asyncio`.

## Global Constraints

- Use `uv` exclusively for package installation, environment management, and running (`uv run ...`).
- Fast boot tasks MUST NOT import heavy LLM or LangGraph libraries on startup.
- Never perform unconfirmed destructive actions (only delete screenshots confirmed as synced to Notion; never auto-delete unapproved downloads or modify Floorp bookmarks without confirmation).
- Strict adherence to TDD: write failing unit test, run to confirm failure, implement minimal code, verify green.

---

### Task 1: Project Scaffolding & Configuration Models with `uv`

**Files:**
- Create: `pyproject.toml`
- Create: `config.yaml`
- Create: `.env.example`
- Create: `deskpilot/__init__.py`
- Create: `deskpilot/config.py`
- Create: `deskpilot/state.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings` model loading from `config.yaml` and environment variables.
- Produces: `BootState` Pydantic model for holding system health, agenda, and agent findings.

- [ ] **Step 1: Initialize project with uv and dependencies**
```bash
uv init --name deskpilot
uv add rich pydantic pydantic-settings pyyaml pytest pytest-asyncio
```

- [ ] **Step 2: Write failing config & state unit tests**
Create `tests/test_config.py` to test loading default settings, overriding paths, and initializing `BootState`.

- [ ] **Step 3: Run test to verify failure**
Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL (modules not found).

- [ ] **Step 4: Implement `deskpilot/config.py` and `deskpilot/state.py`**
Implement Pydantic models for `TempCleanerConfig`, `FloorpConfig`, `CalendarConfig`, `NotionConfig`, `ScreenshotsConfig`, and the composite `Settings` class. Implement `BootState`.

- [ ] **Step 5: Run test to verify it passes**
Run: `uv run pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**
```bash
git add pyproject.toml deskpilot/ tests/ test_config.py
git commit -m "feat(core): project scaffolding, config models, and BootState"
```

---

### Task 2: System Hygiene Task (Windows %TEMP% Cleaner)

**Files:**
- Create: `deskpilot/boot_tasks/system_hygiene.py`
- Create: `deskpilot/boot_tasks/__init__.py`
- Test: `tests/test_system_hygiene.py`

**Interfaces:**
- Consumes: `TempCleanerConfig`
- Produces: `async def clean_temp_directory(temp_path: Path, max_age_hours: int = 24) -> TempCleanResult`

- [ ] **Step 1: Write failing test for temp cleaning**
Create `tests/test_system_hygiene.py` testing temp file removal, directory cleanup, locked file tolerance, and freed byte calculation using mock filesystem.

- [ ] **Step 2: Run test to verify failure**
Run: `uv run pytest tests/test_system_hygiene.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `clean_temp_directory`**
Walk `%TEMP%`, check modification time and file locking, delete unlocked files, remove empty subdirectories, and return structured `TempCleanResult(bytes_freed=..., files_removed=..., errors=...)`.

- [ ] **Step 4: Run test to verify it passes**
Run: `uv run pytest tests/test_system_hygiene.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add deskpilot/boot_tasks/ tests/test_system_hygiene.py
git commit -m "feat(hygiene): safe windows temp cleaner boot task"
```

---

### Task 3: Floorp Bookmark Audit Task

**Files:**
- Create: `deskpilot/boot_tasks/bookmarks.py`
- Test: `tests/test_bookmarks.py`

**Interfaces:**
- Consumes: `FloorpConfig`
- Produces: `async def audit_floorp_bookmarks(profile_path: Path | None = None) -> BookmarkAuditResult`

- [ ] **Step 1: Write failing test for Floorp bookmark audit**
Create `tests/test_bookmarks.py` with mock SQLite database populated with noisy URLs (empty titles or URL-as-title) and duplicate entries.

- [ ] **Step 2: Run test to verify failure**
Run: `uv run pytest tests/test_bookmarks.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `audit_floorp_bookmarks`**
Connect to `places.sqlite` using read-only URI mode (`file:...places.sqlite?immutable=1&mode=ro`). Query `moz_bookmarks` and `moz_places`. Detect noisy titles and group duplicate URLs. Return `BookmarkAuditResult`.

- [ ] **Step 4: Run test to verify it passes**
Run: `uv run pytest tests/test_bookmarks.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add deskpilot/boot_tasks/bookmarks.py tests/test_bookmarks.py
git commit -m "feat(bookmarks): read-only Floorp places.sqlite auditor"
```

---

### Task 4: Package Update Checker (`winget`)

**Files:**
- Create: `deskpilot/boot_tasks/package_checker.py`
- Test: `tests/test_package_checker.py`

**Interfaces:**
- Produces: `async def check_winget_updates(timeout_secs: int = 15) -> WingetUpdateResult`

- [ ] **Step 1: Write failing test for package checker**
Create `tests/test_package_checker.py` mocking `subprocess.create_subprocess_exec` output for `winget upgrade`.

- [ ] **Step 2: Run test to verify failure**
Run: `uv run pytest tests/test_package_checker.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `check_winget_updates`**
Run `winget upgrade --include-unknown` asynchronously. Parse stdout headers and separator lines to extract package names, current versions, and available versions. Return `WingetUpdateResult(updates=[...], total_count=...)`.

- [ ] **Step 4: Run test to verify it passes**
Run: `uv run pytest tests/test_package_checker.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add deskpilot/boot_tasks/package_checker.py tests/test_package_checker.py
git commit -m "feat(packages): asynchronous winget upgrade parser"
```

---

### Task 5: Google Calendar Agenda Briefing

**Files:**
- Create: `deskpilot/boot_tasks/calendar_briefing.py`
- Test: `tests/test_calendar_briefing.py`

**Interfaces:**
- Consumes: `CalendarConfig` (`credentials_path`, `token_path`)
- Produces: `async def fetch_today_agenda(config: CalendarConfig) -> CalendarAgendaResult`

- [ ] **Step 1: Write failing test for calendar agenda parser**
Create `tests/test_calendar_briefing.py` mocking Google Calendar API client returning single-day events, all-day events, and handling missing credentials gracefully.

- [ ] **Step 2: Run test to verify failure**
Run: `uv run pytest tests/test_calendar_briefing.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `calendar_briefing.py`**
Authenticate using `google.oauth2.credentials.Credentials` and `google_auth_oauthlib.flow.InstalledAppFlow` with `calendar.events.readonly` scope. Fetch events from `start_of_today` to `end_of_today` in local time. Return formatted `CalendarAgendaResult(events=[...])`.

- [ ] **Step 4: Run test to verify it passes**
Run: `uv run pytest tests/test_calendar_briefing.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add deskpilot/boot_tasks/calendar_briefing.py tests/test_calendar_briefing.py
git commit -m "feat(calendar): Google Calendar agenda fetcher"
```

---

### Task 6: Interactive Rich Terminal Dashboard & Orchestrator CLI

**Files:**
- Create: `deskpilot/cli.py`
- Modify: `pyproject.toml` (add `[project.scripts] deskpilot = "deskpilot.cli:main"`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `async def run_morning_sequence() -> None` and interactive CLI menu.

- [ ] **Step 1: Write failing test for CLI dashboard workflow**
Create `tests/test_cli.py` mocking Phase 1 tasks, verifying `BootState` aggregation and menu rendering.

- [ ] **Step 2: Run test to verify failure**
Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `deskpilot/cli.py`**
Implement Rich layout with panels for System Hygiene, Floorp Bookmarks, Winget Updates, and Today's Agenda. Present menu choices `[1] Screenshot Triage`, `[2] Downloads Cleanup`, `[3] Clean Floorp Bookmarks`, `[4] Upgrade Winget`, `[0] Exit`.

- [ ] **Step 4: Run test to verify it passes**
Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add deskpilot/cli.py pyproject.toml tests/test_cli.py
git commit -m "feat(cli): rich morning dashboard and boot orchestrator runner"
```

---

### Task 7: Screenshot Triage Agent with LangGraph & Notion

**Files:**
- Create: `deskpilot/agent_tasks/__init__.py`
- Create: `deskpilot/agent_tasks/screenshot_agent/vision.py`
- Create: `deskpilot/agent_tasks/screenshot_agent/notion_sync.py`
- Create: `deskpilot/agent_tasks/screenshot_agent/graph.py`
- Test: `tests/test_screenshot_agent.py`

**Interfaces:**
- Consumes: Screenshots from `Pictures/Screenshots`, Notion API token, Parent Page ID.
- Produces: `create_screenshot_triage_graph() -> CompiledGraph`

- [ ] **Step 1: Add LangGraph, LangChain, and Notion dependencies via uv**
```bash
uv add langgraph langchain-core langchain-google-genai notion-client pillow
```

- [ ] **Step 2: Write failing test for screenshot classification and sync flow**
Create `tests/test_screenshot_agent.py` mocking multimodal model output and Notion API client. Test state transitions, grouping, and that only synced images are flagged for deletion.

- [ ] **Step 3: Run test to verify failure**
Run: `uv run pytest tests/test_screenshot_agent.py -v`
Expected: FAIL.

- [ ] **Step 4: Implement `vision.py`, `notion_sync.py`, and `graph.py`**
Define `ScreenshotState`. Build LangGraph nodes: `scan_images` -> `classify_and_cluster` -> `human_review` -> `upload_to_notion` -> `delete_synced_local`.

- [ ] **Step 5: Run test to verify it passes**
Run: `uv run pytest tests/test_screenshot_agent.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**
```bash
git add deskpilot/agent_tasks/screenshot_agent/ tests/test_screenshot_agent.py
git commit -m "feat(agent): LangGraph screenshot triage agent with Notion sync"
```

---

### Task 8: Downloads Hygiene Agent with LangGraph

**Files:**
- Create: `deskpilot/agent_tasks/downloads_agent/actions.py`
- Create: `deskpilot/agent_tasks/downloads_agent/graph.py`
- Test: `tests/test_downloads_agent.py`

**Interfaces:**
- Consumes: `~/Downloads` directory
- Produces: `create_downloads_hygiene_graph() -> CompiledGraph`

- [ ] **Step 1: Write failing test for downloads triage**
Create `tests/test_downloads_agent.py` testing file scanning, age categorization, duplicate download detection (`* (1).pdf`), and safe deletion approval.

- [ ] **Step 2: Run test to verify failure**
Run: `uv run pytest tests/test_downloads_agent.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `downloads_agent/actions.py` and `graph.py`**
Construct LangGraph nodes: `scan_downloads` -> `analyze_stale_files` -> `propose_plan` -> `execute_approved_actions`.

- [ ] **Step 4: Run test to verify it passes**
Run: `uv run pytest tests/test_downloads_agent.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add deskpilot/agent_tasks/downloads_agent/ tests/test_downloads_agent.py
git commit -m "feat(agent): LangGraph downloads hygiene agent"
```

---

### Task 9: Notion Read-Later Agent

**Files:**
- Create: `deskpilot/agent_tasks/read_later_agent/notion_reader.py`
- Create: `deskpilot/agent_tasks/read_later_agent/graph.py`
- Test: `tests/test_read_later_agent.py`

**Interfaces:**
- Consumes: Notion Database ID for Read-Later
- Produces: `fetch_daily_read_recommendation()` and `mark_article_as_read(page_id)`

- [ ] **Step 1: Write failing test for Read-Later query and update**
Create `tests/test_read_later_agent.py` mocking Notion database query returning `to be read` items and status property patch to `read`.

- [ ] **Step 2: Run test to verify failure**
Run: `uv run pytest tests/test_read_later_agent.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `notion_reader.py` and `graph.py`**
Implement query for unread items with sorting by priority/date. Implement toggle method to transition status to `read`. Integrate reading preview card into the morning dashboard.

- [ ] **Step 4: Run test to verify it passes**
Run: `uv run pytest tests/test_read_later_agent.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add deskpilot/agent_tasks/read_later_agent/ tests/test_read_later_agent.py
git commit -m "feat(agent): Notion read-later agent with status updater"
```

---

### Task 10: Windows Startup Integration & Documentation

**Files:**
- Create: `scripts/register_startup.ps1`
- Create: `README.md`
- Create: `LICENSE` (MIT)

- [ ] **Step 1: Create PowerShell startup registration script**
Implement `scripts/register_startup.ps1` registering a Windows Task Scheduler task or Startup folder shortcut that triggers `uv run deskpilot` on user logon.

- [ ] **Step 2: Write comprehensive open-source README.md**
Include quickstart instructions, prerequisites (`uv`, Google Cloud credentials setup, Notion integration setup), screenshots/mockups, and contribution guidelines.

- [ ] **Step 3: Run full test suite across the entire project**
Run: `uv run pytest -v`
Expected: ALL TESTS PASS.

- [ ] **Step 4: Commit**
```bash
git add scripts/ README.md LICENSE
git commit -m "docs: open source README, license, and windows startup registration"
```
