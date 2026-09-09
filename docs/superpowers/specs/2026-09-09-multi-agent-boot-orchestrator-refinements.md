# DeskPilot Refinements Specification

**Date:** 2026-09-09  
**Status:** Approved for Implementation Planning  
**Target Project:** DeskPilot (`multi-agent-orchestrator-pers1`)

---

## 1. Overview & Objectives

This specification defines four key refinements to the DeskPilot boot orchestrator:
1. **Floorp Bookmarks to On-Demand Only:** Remove the Floorp bookmark audit from the automatic Phase 1 boot sequence. Keep it accessible as an on-demand option `[3]` in the interactive menu or via CLI.
2. **Winget Package Exclusions:** Introduce configurable package exclusion filters (`ignore_packages`) to permanently suppress updates and upgrades for `AdvancedSystemCare` and `RevoUninstallerPro`.
3. **Notion Schema Introspection for "WorkNote":** When syncing screenshots to the `"WorkNote"` database inside `"Work/Self-Dev Notes"`, dynamically query `databases.retrieve` to inspect properties (Title, Tags/Categories, Date, etc.) and map screenshot metadata intelligently.
4. **Multi-Destination Screenshot Routing:** Allow screenshots to be classified and routed directly to distinct Notion destinations:
   - `"WorkNote"` Database (inside `"Work/Self-Dev Notes"`)
   - `"Due Diligence Questionnaire"` Page
   - `"Brainstorm Session"` Page
   - `LOCAL_KEEP` (Unsynced, preserved locally)

---

## 2. Component Design & Changes

### 2.1 Floorp Bookmarks: On-Demand Decoupling
- **Files Modified:** `deskpilot/cli.py`
- **Change:**
  - Remove `audit_floorp_bookmarks` from `run_phase1_boot_sequence()`.
  - Update `render_dashboard()` to render a clean 3-panel morning layout (System Temp, Winget Updates, Calendar Agenda).
  - Menu option `[3]` remains `Clean Floorp Bookmarks (Launch floorp bookmark preview)`, which runs `audit_floorp_bookmarks` and displays the audit table interactively only when requested.

### 2.2 Winget Exclusions (`AdvancedSystemCare` & `RevoUninstallerPro`)
- **Files Modified:** `deskpilot/config.py`, `deskpilot/boot_tasks/package_checker.py`, `config.yaml`
- **Change:**
  - Add `ignore_packages: list[str] = Field(default_factory=lambda: ["AdvancedSystemCare", "RevoUninstallerPro"])` to `WingetConfig`.
  - In `parse_winget_output(..., ignore_packages=None)`, filter out any package whose `name` or `id` matches an ignored entry (case-insensitive substring match).
  - In `execute_menu_action("4")`, ensure ignored packages are excluded from the interactive upgrade command.

### 2.3 Notion Schema Introspection & Multi-Destination Routing
- **Files Modified:** `deskpilot/config.py`, `deskpilot/agent_tasks/screenshot_agent/vision.py`, `deskpilot/agent_tasks/screenshot_agent/notion_sync.py`, `deskpilot/agent_tasks/screenshot_agent/graph.py`
- **Configuration (`config.yaml`):**
  ```yaml
  notion:
    token: ""
    read_later_database_id: ""  # "Read Later Newsfeed"
    screenshot_destinations:
      work_notes_database_id: ""    # "WorkNote" database inside "Work/Self-Dev Notes"
      due_diligence_page_id: ""     # "Due Diligence Questionnaire" page
      brainstorm_page_id: ""        # "Brainstorm Session" page
  ```
- **Vision Model Classification:**
  - The vision prompt categorizes images into:
    - `WORK_NOTES`: Code, architecture, developer tools, technical diagrams, debugging notes.
    - `DUE_DILIGENCE`: Questionnaires, compliance tables, vendor audits, checklists.
    - `BRAINSTORM`: Whiteboards, UI sketches, wireframes, new ideas.
    - `LOCAL_KEEP`: Memes, temporary chats, receipts, personal desktop snapshots.
- **Intelligent Database Properties Adapter:**
  - In `notion_sync.py`, for destinations that are databases (e.g. `work_notes_database_id`), call `client.databases.retrieve(database_id=...)`.
  - Detect title property name (e.g. `Name` vs `Title`).
  - Detect multi-select / select property (e.g. `Tags`, `Category`, `Topic`). If present, populate with relevant tag.
  - Detect date property (e.g. `Date`, `Created`).
  - Create the page with mapped properties and append text/image blocks in page content.
  - For page destinations (`due_diligence_page_id`, `brainstorm_page_id`), append child blocks or child pages cleanly.

---

## 3. Verification & Safety Safeguards

1. **Safety:** Screenshots classified as `LOCAL_KEEP` or belonging to unconfigured destination IDs are **never deleted locally**.
2. **Fast Boot Preservation:** Zero LLM/LangGraph imports in Phase 1 boot sequence.
3. **Unit Tests:**
   - Tests for `WingetConfig.ignore_packages` filtering out `AdvancedSystemCare` and `RevoUninstallerPro`.
   - Tests for CLI Phase 1 boot sequence without Floorp bookmark task.
   - Tests for Notion database schema introspection and property mapping with mock database schemas.
   - Tests for multi-destination routing in the screenshot triage graph.
