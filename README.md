# DeskPilot

> **Personal Multi-Agent Morning Command Center & Boot Orchestrator**

DeskPilot is an intelligent personal productivity orchestrator designed to run upon logging into your computer. It pairs **sub-second parallel morning hygiene checks** with **on-demand LangGraph autonomous agents** to organize your digital workspace before you start your day.

---

## Architecture Overview

DeskPilot utilizes a **hybrid execution model**:
1. **Phase 1: Sub-Second Boot Sequence** &mdash; Runs fast, local, lightweight hygiene tasks concurrently using `asyncio.gather`. Never imports heavy LLM or LangGraph libraries on startup to ensure instant terminal boot times.
2. **Phase 2: On-Demand LangGraph Agents** &mdash; Dynamically loaded only when triggered by user selection. Powered by LangGraph, multimodal LLMs (Google Gemini), and external APIs (Notion, Google Calendar).

```mermaid
flowchart TD
    Logon[Windows Logon / CLI Startup] --> CLI[DeskPilot Boot Orchestrator]
    
    subgraph Phase1["Phase 1: Sub-Second Fast Boot Sequence (asyncio)"]
        CLI --> TH[System %TEMP% Cleaner]
        CLI --> WU[Winget Package Updates Check]
        CLI --> GC[Google Calendar Today's Agenda]
    end

    Phase1 --> Dashboard[3-Panel Rich Terminal Dashboard]

    subgraph Phase2["Phase 2: Interactive Action Menu & Intelligent Agents"]
        Dashboard --> Menu{User Action}
        Menu -->|[1]| STA[Screenshot Triage Agent\nLangGraph + Gemini Vision + Notion Multi-Routing]
        Menu -->|[2]| DHA[Downloads Hygiene Agent\nLangGraph + Auto-Archive + Cleaner]
        Menu -->|[3]| FBA[Floorp Bookmarks Auditor (On-Demand)\nplaces.sqlite Read-Only Duplicate & Noise Inspector]
        Menu -->|[4]| WGU[Interactive Winget Package Upgrade\nWith Custom Exclusions]
        Menu -->|[5]| RLA[Notion Read-Later Digest\nCurated Reading Pick & Status Update]
        Menu -->|[0]| Exit[Dismiss & Exit]
    end
```

---

## Core Features

### Fast Boot Sequence (Phase 1)
- **Windows `%TEMP%` Cleaner**: Recursively purges temporary files older than 24 hours while safely tolerating locked Windows process handles.
- **Winget Package Updates**: Queries Windows Package Manager for available software upgrades with timeout protection and custom package exclusions (e.g. ignoring `AdvancedSystemCare` and `RevoUninstallerPro`).
- **Google Calendar Agenda**: Fetches your daily schedule, displaying all-day events, meeting times, and Google Meet locations.

### Intelligent On-Demand Agents & Tools (Phase 2)
- **Screenshot Triage Agent (Option 1)**: Scans your Screenshots folder, analyzes images using Google Gemini Vision, categorizes them into smart clusters, and dynamically routes them to designated Notion targets (WorkNote database with dynamic schema introspection, Due Diligence Questionnaire page, Brainstorm Session page, or Local Keep) with strict safety guarantees (only confirmed synced items are cleaned locally).
- **Downloads Hygiene Agent (Option 2)**: Classifies downloads into installers, archives, code, documents, media, and images. Automatically proposes deleting stale installers (>30 days old) and archiving unorganized files with user confirmation.
- **On-Demand Floorp Bookmarks Auditor (Option 3)**: Decoupled from the boot sequence for sub-second startup; reads Floorp's `places.sqlite` in read-only immutable mode (`?immutable=1&mode=ro`) on demand, detecting noisy tracking parameters (UTMs, referral tags) and duplicate bookmark groups without browser locking.
- **Interactive Winget Package Upgrade (Option 4)**: Interactive package upgrade CLI helper that automatically respects your configured exclusions (`ignore_packages`).
- **Notion Read-Later Digest (Option 5)**: Curates daily unread reading recommendations from your Notion reading list ("The Read Later List"), prioritizing critical tags and oldest items with one-click status updates.

---

## Terminal Dashboard Preview

DeskPilot launches with a clean **3-panel morning boot dashboard** (System Hygiene, Package Updates, and Today's Agenda):

```text
╭───────────────────────────── DeskPilot Morning Command Center ─────────────────────────────╮
│                                Wednesday, September 09, 2026 - 09:00 AM                     │
╰────────────────────────────────────────────────────────────────────────────────────────────╯
╭── System Hygiene (%TEMP%) ──╮ ╭── Package Updates (winget) ─╮
│ Bytes Freed: 15.0 MB        │ │ 2 update(s) available:      │
│ Files Removed: 42           │ │ • Git (2.43.0 -> 2.44.0)    │
│ Dirs Removed: 5             │ │ • Neovim (0.9.4 -> 0.10.0)  │
╰─────────────────────────────╯ ╰─────────────────────────────╯
╭── Today's Agenda (Google Calendar) ────────────────────────────────────────────────────────╮
│ 2 event(s) scheduled (Wednesday, Sep 09):                                                  │
│ • [09:00 - 09:30] Team Standup (Google Meet)                                               │
│ • [14:00 - 15:00] Architecture Review (Conf Room A)                                        │
╰────────────────────────────────────────────────────────────────────────────────────────────╯

╭── Action Menu ─────────────────────────────────────────────────────────────────────────────╮
│ [1]  Triage Screenshots (Sort to Notion & cleanup)                                          │
│ [2]  Downloads Folder Cleanup (Smart categorize & delete advice)                           │
│ [3]  Clean Floorp Bookmarks (Launch floorp bookmark preview)                               │
│ [4]  Upgrade Winget Packages (Execute interactive winget upgrade)                          │
│ [5]  Notion Read-Later Digest (Preview pick & mark read)                                   │
│ [0]  Dismiss & Exit                                                                        │
╰────────────────────────────────────────────────────────────────────────────────────────────╯
Select an option [0-5]: 
```

> **On-Demand Floorp View**: When option `[3]` is selected, Floorp bookmarks audit executes on-demand without slowing down your initial boot. When bookmarks data is present, the dashboard seamlessly expands to a 2x2 grid displaying the Floorp Bookmarks panel alongside System Hygiene, Winget, and Calendar.

---

## Prerequisites

- **Operating System**: Windows 10 / 11 (64-bit)
- **Python**: Python 3.11 or newer
- **Package Manager**: [`uv`](https://docs.astral.sh/uv/) (Astral's ultra-fast Python package manager)
- **PowerShell**: Windows PowerShell 5.1+ or PowerShell 7+ (for startup integration scripts)

---

## Quickstart Guide

### 1. Install `uv`
If you haven't installed `uv` yet, run in PowerShell:
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 2. Clone & Setup Project
```powershell
git clone https://github.com/your-username/deskpilot.git
cd deskpilot

# Sync dependencies and create isolated virtual environment
uv sync
```

### 3. Configure Settings
Copy `.env.example` to `.env`:
```powershell
Copy-Item .env.example .env
```
Edit `.env` and `config.yaml` to configure your API tokens and directories (see [Configuration Reference](#configuration-reference)).

### 4. Run DeskPilot
```powershell
# Interactive Command Center
uv run deskpilot

# Non-interactive automated boot run (executes Phase 1 hygiene and exits)
uv run deskpilot --startup
```

---

## Service Integrations Setup

### 1. Google Calendar Integration
1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (e.g. `DeskPilot`).
3. Enable the **Google Calendar API** under **APIs & Services > Library**.
4. Go to **APIs & Services > Credentials** and click **Create Credentials > OAuth client ID**.
5. Choose **Desktop app** as the Application type.
6. Download the client secret JSON file and save it as `credentials.json` in the root of the DeskPilot repository.
7. On first run, DeskPilot will prompt you once to authorize calendar access in your browser and automatically save `token.json`.

### 2. Notion API Integration
DeskPilot connects with Notion to power both the Screenshot Triage Agent and the Read-Later Newsfeed Agent.

1. Visit [Notion My Integrations](https://www.notion.so/profile/integrations).
2. Click **New integration**, name it `DeskPilot`, and select your workspace.
3. Copy the **Internal Integration Secret** and paste it into `.env` as `NOTION_TOKEN`.
4. Open the target Notion databases or pages you wish to sync with DeskPilot, click **... > Connect to**, and select your `DeskPilot` integration.

#### "The Read Later List" (Read-Later Newsfeed)
- Connect your reading list database to DeskPilot and set `read_later_database_id` in `config.yaml` (or `NOTION_READ_LATER_DATABASE_ID` in `.env`).
- Menu Option `[5]` analyzes your unread articles from "The Read Later List", prioritizes high-value tags (e.g., `#critical`, `#architecture`) and oldest entries, presents daily picks in your terminal, and lets you mark them as read in Notion.

#### Multi-Destination Screenshot Routing
DeskPilot's Screenshot Triage Agent uses Gemini Vision to classify captured images and route them to dedicated Notion destinations:
- **WorkNote Database** (`work_notes_database_id` / `NOTION_WORK_NOTES_DATABASE_ID`): Receives `WORK_NOTES` screenshots.
- **Due Diligence Questionnaire Page** (`due_diligence_page_id` / `NOTION_DUE_DILIGENCE_PAGE_ID`): Receives `DUE_DILIGENCE` screenshots, appending formatted callout blocks.
- **Brainstorm Session Page** (`brainstorm_page_id` / `NOTION_BRAINSTORM_PAGE_ID`): Receives `BRAINSTORM` screenshots, appending formatted callout blocks.
- **Local Keep (`LOCAL_KEEP`)**: Preserved strictly in local storage without syncing to Notion or deleting.
- *(Fallback)*: `parent_page_id` / `NOTION_PARENT_PAGE_ID` is retained for backwards compatibility when individual destination IDs are not configured.

#### Dynamic Notion Schema Introspection for WorkNote
When syncing to the **WorkNote** database, DeskPilot never relies on hardcoded property names. Instead, it dynamically introspects your database schema via `client.databases.retrieve`:
- **Title Property**: Detects the database title property regardless of whether it is named `Name`, `Title`, or `Topic`.
- **Category / Tag Property**: Inspects property types for `select` or `multi_select` fields (matching `Tags`, `Category`, `Topic`, `Area`, etc.) and formats the payload with appropriate tag arrays or objects.
- **Date Property**: Detects `date` fields (matching `Date`, `Created`, `Date Created`, etc.) and automatically assigns the current UTC timestamp.
- **Content Blocks**: Appends structured Notion child blocks including a heading with the note title, category badge, AI rationale, and source image filename.
- **Schema Caching**: Results are cached in-memory per run to eliminate redundant Notion API roundtrips.

### 3. Google Gemini Vision API
1. Get a Gemini API key from [Google AI Studio](https://aistudio.google.com/).
2. Add the key to `.env`:
   ```dotenv
   GEMINI_API_KEY=AIzaSy...
   ```

### 4. Floorp Browser Bookmarks (On-Demand)
- Floorp bookmark auditing is executed **on-demand** via Action Menu option `[3]`, completely decoupled from the boot sequence for sub-second startup speed.
- DeskPilot automatically searches `%APPDATA%\Floorp\Profiles\*.default-release\places.sqlite`.
- If using a custom Floorp installation or profile, set `floorp.profile_path` in `config.yaml`.
- The connection uses SQLite's read-only immutable flag (`?immutable=1&mode=ro`), allowing you to audit bookmarks even while Floorp is open without lock contention or write hazards.

### 5. Winget Package Exclusions
To keep your morning boot dashboard clean and avoid repetitive prompts for software you manage separately or keep pinned, configure `winget.ignore_packages` in `config.yaml`:
- By default, DeskPilot ignores:
  - `AdvancedSystemCare`
  - `RevoUninstallerPro`
- Exclusions perform case-insensitive substring matching against both package name and package ID (e.g. matching `RevoUninstallerPro` or `IObit.AdvancedSystemCare`).
- Excluded packages are filtered out from both the morning update notification panel and the interactive winget upgrade helper (Option `[4]`).

---

## Windows Startup Integration

DeskPilot includes PowerShell automation scripts in the `scripts/` directory to automatically launch the morning command center whenever you log into Windows.

### Register Startup Task
Run the registration script from PowerShell:
```powershell
# Register interactive startup task (launches on logon)
.\scripts\register_startup.ps1

# Register non-interactive background check
.\scripts\register_startup.ps1 -StartupFlag

# Use Windows Startup folder shortcut instead of Task Scheduler
.\scripts\register_startup.ps1 -Method StartupFolder

# Overwrite existing registration
.\scripts\register_startup.ps1 -Force
```

### Unregister Startup Task
To remove DeskPilot from Windows startup at any time:
```powershell
.\scripts\unregister_startup.ps1
```

---

## Configuration Reference

### `config.yaml`
```yaml
# %TEMP% directory cleaner
temp_cleaner:
  enabled: true
  max_age_hours: 24
  temp_path: null     # null defaults to Windows %TEMP%

# Floorp browser bookmark auditor (on-demand via menu option [3])
floorp:
  enabled: true
  profile_path: null  # null auto-locates places.sqlite

# Google Calendar morning agenda
calendar:
  enabled: true
  credentials_path: credentials.json
  token_path: token.json
  calendar_id: primary
  max_results: 20

# Notion workspace integration
notion:
  enabled: true
  token: ''
  parent_page_id: ''              # Legacy / fallback parent page
  read_later_database_id: ''      # "The Read Later List" newsfeed database ID
  screenshot_destinations:
    work_notes_database_id: ''    # Database ID for WorkNote (with dynamic schema introspection)
    due_diligence_page_id: ''     # Page ID for Due Diligence Questionnaire
    brainstorm_page_id: ''        # Page ID for Brainstorm Session

# Screenshots folder triage
screenshots:
  enabled: true
  directory: ~/Pictures/Screenshots
  delete_synced_local: true

# Downloads folder hygiene
downloads:
  enabled: true
  directory: ~/Downloads
  stale_days: 30

# Winget package updater & exclusions
winget:
  enabled: true
  timeout_secs: 15
  ignore_packages:
    - AdvancedSystemCare
    - RevoUninstallerPro
```

### Environment Variables (`.env`)
Environment variables override settings loaded from `config.yaml`:

| Variable | Description |
| :--- | :--- |
| `NOTION_TOKEN` | Notion Internal Integration Secret |
| `NOTION_PARENT_PAGE_ID` | Notion Page ID for screenshot notes sync (fallback) |
| `NOTION_READ_LATER_DATABASE_ID` | Notion Database ID for "The Read Later List" |
| `NOTION_WORK_NOTES_DATABASE_ID` | Notion Database ID for "WorkNote" entries (dynamic schema introspection) |
| `NOTION_DUE_DILIGENCE_PAGE_ID` | Notion Page ID for "Due Diligence Questionnaire" screenshots |
| `NOTION_BRAINSTORM_PAGE_ID` | Notion Page ID for "Brainstorm Session" screenshots |
| `GEMINI_API_KEY` | Google Gemini API key for vision classification |
| `LANGCHAIN_TRACING_V2` | Set to `true` to enable LangSmith workflow tracing |
| `LANGCHAIN_API_KEY` | LangSmith API Key |

---

## Safety & Privacy Guarantees

- **Multi-Destination Screenshot Routing & Zero Unsynced Deletion**: The Screenshot Triage Agent **only** removes local screenshots that have been verified as successfully created and confirmed in their designated Notion destination (`is_synced == True`). Images classified as `LOCAL_KEEP` or unapproved clusters are strictly preserved locally and never deleted.
- **Read-Only SQLite Locking**: The Floorp auditor connects to `places.sqlite` using `immutable=1&mode=ro` on demand. It never locks the database, allowing you to use your browser freely during audits.
- **Winget Exclusion Protection**: Pinned or blacklisted software packages (`AdvancedSystemCare`, `RevoUninstallerPro`) are automatically filtered out from upgrade lists to prevent unwanted bulk modifications.
- **No Silent Bulk File Destruction**: The Downloads Hygiene Agent analyzes and suggests actions, but **never** deletes or moves files without explicit user approval.
- **Local-First Fast Boot**: All Phase 1 boot checks execute strictly locally on your machine without making heavy network calls or loading LLM libraries.

---

## Development & Testing

DeskPilot is built with strict Test-Driven Development (TDD) principles.

```powershell
# Run the complete test suite
uv run pytest -v

# Run specific test modules
uv run pytest tests/test_cli.py -v
uv run pytest tests/test_package_checker.py -v
uv run pytest tests/test_screenshot_agent.py -v
uv run pytest tests/test_downloads_agent.py -v
uv run pytest tests/test_read_later_agent.py -v
uv run pytest tests/test_system_hygiene.py -v
uv run pytest tests/test_bookmarks.py -v
uv run pytest tests/test_calendar_briefing.py -v
uv run pytest tests/test_startup_scripts.py -v
uv run pytest tests/test_config.py -v
```

---

## Contributing

Contributions are welcome! Please feel free to open issues or submit pull requests:
1. Fork the repository.
2. Create a feature branch: `git checkout -b feat/my-new-feature`.
3. Ensure all tests pass: `uv run pytest -v`.
4. Commit your changes: `git commit -m "feat: add support for new browser"`.
5. Push to the branch and open a Pull Request.

---

## License

This project is licensed under the [MIT License](LICENSE).
