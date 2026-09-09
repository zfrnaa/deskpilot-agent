# Multi-Agent Boot Orchestrator Design Specification

**Date:** 2026-09-09  
**Status:** Approved for Implementation Planning  
**Target Environment:** Windows 11, Python 3.11+, `uv` package manager  

---

## 1. Overview & Objective

The **Multi-Agent Boot Orchestrator** is a personal assistant script designed to run automatically whenever the user boots their Windows PC (via Windows Task Scheduler or the Startup folder). 

It optimizes the morning routine through a two-phase architecture:
1. **Phase 1 (Automated Morning Hygiene - Sub-second, non-LLM):** Runs fast parallel background checks (Floorp browser bookmarks audit, Windows `%TEMP%` cleanup, `winget` package update check, and Google Calendar agenda briefing).
2. **Phase 2 (Interactive Rich Dashboard & On-Demand Agents):** Presents a clean summary in an interactive terminal. Allows the user to trigger intelligent LangGraph agents (Screenshot Triage to Notion, Downloads hygiene, Floorp bookmark deduplication/renaming) or cleanly exit without consuming persistent background resources.

---

## 2. Architecture & Design Principles

### 2.1 Hybrid Execution Model
To avoid the multi-second import latency of heavy LLM and graph libraries on Windows startup, the project is divided into two distinct tiers:
- **Fast Tier (Boot Tasks):** Pure standard library + lightweight clients (`asyncio`, `google-api-python-client`, `rich`). Zero LLM imports.
- **Intelligent Tier (LangGraph Agents):** Lazy-loaded only when the user selects a specific agent from the interactive menu. Full LangSmith tracing enabled (`LANGCHAIN_TRACING_V2=true`).

### 2.2 Process Lifecycle
- **No persistent daemon:** The orchestrator runs at boot, gathers data, presents options, executes any selected tasks, and cleanly terminates. It does not stay resident in background memory.
- Can be invoked manually anytime from terminal: `uv run orchestrator`.

---

## 3. Directory & Module Structure

```text
multi-agent-orchestrator-pers1/
├── pyproject.toml
├── .env.example
├── config.yaml
├── credentials.json               # Google OAuth client secrets (ignored in git)
├── token.json                     # Cached Google OAuth access/refresh token
├── docs/
│   └── superpowers/specs/
│       └── 2026-09-09-multi-agent-boot-orchestrator-design.md
├── orchestrator/
│   ├── __init__.py
│   ├── cli.py                     # Entrypoint & Rich interactive dashboard
│   ├── config.py                  # Pydantic settings & config loader
│   ├── state.py                   # Pydantic data structures for dashboard state
│   │
│   ├── boot_tasks/                # Fast, parallel non-LLM tasks
│   │   ├── __init__.py
│   │   ├── bookmarks.py           # Floorp places.sqlite read-only check
│   │   ├── system_hygiene.py      # %TEMP% cleaner with safe locked-file skip
│   │   ├── package_checker.py     # Read-only `winget upgrade` parser
│   │   └── calendar_briefing.py   # Google Calendar API agenda reader
│   │
│   └── agent_tasks/               # LangGraph intelligent agents (lazy loaded)
│       ├── __init__.py
│       ├── screenshot_agent/
│       │   ├── graph.py           # LangGraph workflow for triage & upload
│       │   ├── vision.py          # Vision LLM classification & clustering
│       │   └── notion_sync.py     # Notion API page/block creator
│       ├── downloads_agent/
│       │   ├── graph.py           # LangGraph organizer for ~/Downloads
│       │   └── actions.py         # File archiver/trash with safety confirm
│       └── read_later_agent/
│           ├── graph.py           # LangGraph picker for unread items
│           └── notion_reader.py   # Notion query and status updater
│
└── tests/
    ├── test_boot_tasks.py
    ├── test_screenshot_agent.py
    └── test_config.py
```

---

## 4. Detailed Component Specifications

### 4.1 Fast Boot Tasks (Phase 1)

All Phase 1 tasks execute concurrently via `asyncio.gather()`:

1. **System Hygiene (`system_hygiene.py`):**
   - Targets Windows `%TEMP%` (`C:\Users\<user>\AppData\Local\Temp`).
   - Removes files and empty directories not locked by running processes.
   - Calculates total bytes freed.

2. **Floorp Bookmark Audit (`bookmarks.py`):**
   - Connects to Floorp's profile (`places.sqlite`) in `immutable=1` URI / read-only mode to prevent database locks.
   - Scans for noisy URLs without clean titles and detects duplicate URL groups.
   - Outputs status count for the dashboard.

3. **Package Update Audit (`package_checker.py`):**
   - Spawns `winget upgrade --include-unknown` with a 15-second timeout.
   - Parses the table output to extract counts of upgradeable packages.
   - Does NOT apply upgrades automatically on boot.

4. **Google Calendar Agenda (`calendar_briefing.py`):**
   - Uses OAuth 2.0 with scope `https://www.googleapis.com/auth/calendar.events.readonly`.
   - On first launch, performs local browser OAuth and saves `token.json`. Subsequent runs use silent token refresh.
   - Fetches events between today 00:00:00 and today 23:59:59 in the local timezone.
   - Formats a clean chronological agenda list.

---

### 4.2 Intelligent LangGraph Agents (Phase 2)

#### A. Screenshot Triage Agent (`screenshot_agent`)
- **Input Directory:** `C:\Users\<user>\Pictures\Screenshots`.
- **LangGraph Nodes:**
  1. `scan_screenshots`: Collects `.png`, `.jpg` files not yet recorded in the local processed registry.
  2. `vision_triage`: Sends image batches to a multimodal LLM (e.g. Gemini 2.5 Flash / GPT-4o-mini).
     - Returns JSON: `{"title": str, "classification": "NOTION_NOTE" | "LOCAL_KEEP", "cluster_tag": str, "rationale": str}`.
  3. `human_review_node`: Displays a formatted table of classified screenshots:
     - Previews clustered Notion notes vs. skipped images.
     - User confirms `[Y/n]` or modifies classifications.
  4. `notion_upload_node`: Creates or appends Notion blocks/pages under the user's specified Notion parent page/database.
  5. `cleanup_synced_node`: **Deletes only the successfully uploaded local screenshots.** Any image classified as `LOCAL_KEEP` or rejected by the user remains untouched.

#### B. Downloads Folder Hygiene Agent (`downloads_agent`)
- **Input Directory:** `C:\Users\<user>\Downloads`.
- **LangGraph Nodes:**
  1. `scan_downloads`: Catalogs files by age, file type, and size.
  2. `analyze_files`: Identifies stale installers (`.exe`, `.msi` > 7 days old), temporary archive extractions, and duplicate downloads (`* (1).pdf`).
  3. `recommend_cleanup`: Generates categorized suggestions (e.g. "Safe to delete: 4.2 GB of old installers").
  4. `execute_actions`: Performs file deletion or moving to `Downloads/Archive` upon explicit user confirmation.

#### C. Notion Read-Later Digest (`read_later_agent`)
- **Target:** Notion database specified in `config.yaml` (`NOTION_READ_LATER_DB_ID`).
- **Functionality:**
  - Queries entries where `Status == "to be read"`.
  - Recommends the top 1–2 items for the day on the morning dashboard.
  - Interactive shortcut (`[R]`) allows the user to mark an item as `"read"`, which immediately updates the Notion page property via Notion API.

---

## 5. User Interface & Morning Flow

When Windows launches the script on boot:

```text
============================================================
              ☀️ MORNING BOOT ORCHESTRATOR
============================================================
 [✓] System Temp:    850 MB freed
 [✓] Floorp Marks:   22 noisy titles, 15 duplicate groups
 [!] Winget:         3 updates available (Git, Node.js, VSCode)
------------------------------------------------------------
 📅 TODAY'S AGENDA (Google Calendar)
 • 10:00 AM - 10:30 AM : Team Standup
 • 02:00 PM - 03:00 PM : Architecture Review
------------------------------------------------------------
 📖 TODAY'S READ-LATER PICK (Notion)
 • "Understanding LangGraph State Reducers" [To Be Read]
   (Open link: https://notion.so/... | Press [R] to mark as Read)
============================================================
Select an action to run:
 [1] Triage Screenshots (Analyze, sort to Notion & clean up)
 [2] Downloads Folder Cleanup (Smart categorize & delete advice)
 [3] Run Floorp Bookmark Renamer & Deduplicator
 [4] Upgrade Winget Packages
 [0] Dismiss & Exit (Default after timeout)
Choice [0-4]: 
```

---

## 6. Verification & Safety Safeguards

1. **Safety Backups:** Any write operations to Floorp's `places.sqlite` create a timestamped copy (`places.sqlite.bak_<timestamp>`) before modifying.
2. **Read-only Defaults:** Boot tasks are strictly read-only for third-party databases and packages (`winget upgrade` only queries; Floorp is only inspected).
3. **No Unapproved File Deletion:** Screenshot deletion only occurs for items verified to have been successfully written to Notion. Downloads cleanup requires confirmation.
4. **Unit Tests:**
   - Isolated unit tests for temp cleaner, package parser, and calendar briefing with mocked APIs.
   - LangGraph graph validation tests with mocked LLM and Notion responses.
