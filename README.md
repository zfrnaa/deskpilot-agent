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
        CLI --> FA[Floorp Bookmarks Audit places.sqlite]
        CLI --> WU[Winget Package Updates Check]
        CLI --> GC[Google Calendar Today's Agenda]
    end

    Phase1 --> Dashboard[Rich Terminal Dashboard]

    subgraph Phase2["Phase 2: Interactive Action Menu & Intelligent Agents"]
        Dashboard --> Menu{User Action}
        Menu -->|[1]| STA[Screenshot Triage Agent\nLangGraph + Gemini Vision + Notion]
        Menu -->|[2]| DHA[Downloads Hygiene Agent\nLangGraph + Auto-Archive + Cleaner]
        Menu -->|[3]| FBP[Floorp Bookmarks Preview\nNoisy URLs + Duplicate Inspector]
        Menu -->|[4]| WGU[Interactive Winget Package Upgrade]
        Menu -->|[0]| Exit[Dismiss & Exit]
    end
```

---

## Core Features

### Fast Boot Sequence (Phase 1)
- **Windows `%TEMP%` Cleaner**: Recursively purges temporary files older than 24 hours while safely tolerating locked Windows process handles.
- **Floorp Bookmark Auditor**: Reads Floorp's `places.sqlite` in read-only immutable mode (`?immutable=1&mode=ro`), detecting noisy tracking parameters and duplicate bookmark groups without browser locking.
- **Winget Package Updates**: Queries Windows Package Manager for available software upgrades with timeout protection.
- **Google Calendar Agenda**: Fetches your daily schedule, displaying all-day events, meeting times, and Google Meet locations.

### Intelligent On-Demand Agents (Phase 2)
- **Screenshot Triage Agent (Option 1)**: Scans your Screenshots folder, analyzes images using Google Gemini Vision, categorizes them into smart clusters, and syncs note-worthy screenshots to Notion with strict safety guarantees (only confirmed synced items are cleaned locally).
- **Downloads Hygiene Agent (Option 2)**: Classifies downloads into installers, archives, code, documents, media, and images. Automatically proposes deleting stale installers (>30 days old) and archiving unorganized files with user confirmation.
- **Floorp Bookmarks Preview (Option 3)**: Interactive preview of noisy tracking bookmarks and duplicate groups for browser cleanup.
- **Notion Read-Later Digest**: Curates daily unread reading recommendations from your Notion reading list, prioritizing critical tags and oldest items.

---

## Terminal Dashboard Preview

```text
╭───────────────────────────── DeskPilot Morning Command Center ─────────────────────────────╮
│                                Wednesday, September 09, 2026 - 09:00 AM                     │
╰────────────────────────────────────────────────────────────────────────────────────────────╯
╭── System Hygiene (%TEMP%) ──╮ ╭── Floorp Bookmarks ─────────╮
│ Bytes Freed: 15.0 MB        │ │ Total Bookmarks: 120        │
│ Files Removed: 42           │ │ Noisy URLs (Tracking): 7    │
│ Dirs Removed: 5             │ │ Duplicate Groups: 2         │
╰─────────────────────────────╯ ╰─────────────────────────────╯
╭── Package Updates (winget) ─╮ ╭── Today's Agenda (Google) ──╮
│ 2 update(s) available:      │ │ 2 event(s) scheduled:       │
│ • Git (2.43.0 -> 2.44.0)    │ │ • [09:00 - 09:30] Standup   │
│ • Neovim (0.9.4 -> 0.10.0)  │ │ • [14:00 - 15:00] Review    │
╰─────────────────────────────╯ ╰─────────────────────────────╯

╭── Action Menu ─────────────────────────────────────────────────────────────────────────────╮
│ [1]  Triage Screenshots (Sort to Notion & cleanup)                                          │
│ [2]  Downloads Folder Cleanup (Smart categorize & delete advice)                           │
│ [3]  Clean Floorp Bookmarks (Launch floorp bookmark preview)                               │
│ [4]  Upgrade Winget Packages (Execute interactive winget upgrade)                          │
│ [0]  Dismiss & Exit                                                                        │
╰────────────────────────────────────────────────────────────────────────────────────────────╯
Select an option [0-4]: 
```

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
1. Visit [Notion My Integrations](https://www.notion.so/profile/integrations).
2. Click **New integration**, name it `DeskPilot`, and select your workspace.
3. Copy the **Internal Integration Secret** and paste it into `.env` as `NOTION_TOKEN`.
4. Open the Notion parent page where you want screenshots or notes stored.
5. Click **... > Connect to** and select your `DeskPilot` integration.
6. Copy the Page ID from the URL and set `NOTION_PARENT_PAGE_ID` in `.env`.
7. (Optional) For the Read-Later Agent, share your Read-Later database with the integration and set `NOTION_READ_LATER_DATABASE_ID`.

### 3. Google Gemini Vision API
1. Get a Gemini API key from [Google AI Studio](https://aistudio.google.com/).
2. Add the key to `.env`:
   ```dotenv
   GEMINI_API_KEY=AIzaSy...
   ```

### 4. Floorp Browser Bookmarks
- DeskPilot automatically searches `%APPDATA%\Floorp\Profiles\*.default-release\places.sqlite`.
- If using a custom Floorp installation, set `floorp.profile_path` in `config.yaml`.

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

# Floorp browser bookmark auditor
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
  parent_page_id: ''
  read_later_database_id: ''

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

# Winget package updater
winget:
  enabled: true
  timeout_secs: 15
```

### Environment Variables (`.env`)
Environment variables override settings loaded from `config.yaml`:

| Variable | Description |
| :--- | :--- |
| `NOTION_TOKEN` | Notion Internal Integration Secret |
| `NOTION_PARENT_PAGE_ID` | Notion Page ID for screenshot notes sync |
| `NOTION_READ_LATER_DATABASE_ID` | Notion Database ID for Read-Later articles |
| `GEMINI_API_KEY` | Google Gemini API key for vision classification |
| `LANGCHAIN_TRACING_V2` | Set to `true` to enable LangSmith workflow tracing |
| `LANGCHAIN_API_KEY` | LangSmith API Key |

---

## Safety & Privacy Guarantees

- **No Premature Deletion**: The Screenshot Triage Agent **only** removes local screenshots that have been verified as successfully created and confirmed in Notion.
- **Read-Only SQLite Locking**: The Floorp auditor connects to `places.sqlite` using `immutable=1&mode=ro`. It never locks the database, allowing you to use your browser freely during boot checks.
- **No Silent Bulk File Destruction**: The Downloads Hygiene Agent analyzes and suggests actions, but **never** deletes or moves files without explicit user approval.
- **Local First**: All Phase 1 boot checks execute strictly locally on your machine without making external network calls (except for Google Calendar and winget update checks).

---

## Development & Testing

DeskPilot is built with strict Test-Driven Development (TDD) principles.

```powershell
# Run the complete test suite
uv run pytest -v

# Run specific test modules
uv run pytest tests/test_cli.py -v
uv run pytest tests/test_startup_scripts.py -v
uv run pytest tests/test_screenshot_agent.py -v
uv run pytest tests/test_downloads_agent.py -v
uv run pytest tests/test_read_later_agent.py -v
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
