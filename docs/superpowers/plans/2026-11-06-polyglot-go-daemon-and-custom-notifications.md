# Polyglot Go Daemon & Clean Bento Notification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Python background daemon loop with an ultra-lightweight, native Go daemon (`deskpilot-daemon.exe`, ~8MB RAM) that monitors the screenshot buffer ($\ge 10$) and 5-day downloads cadence, fires notifications (Windows Toast or custom Bento card), and triggers the full Python AI agent on-demand.

**Architecture:** Thin Watcher + Fat Worker pattern. The Go daemon handles 24/7 background directory watching and scheduling at near-zero resource cost. It communicates with DeskPilot via CLI flags (`uv run deskpilot ...`) and reads/writes state in the shared SQLite database (`storage.db`).

**Tech Stack:** Go 1.27 (`modernc.org/sqlite`, `fsnotify`, `getlantern/systray`), Python 3.14 (DeskPilot core, LangGraph, Rich/Textual), Windows Task Scheduler, Windows Win32 API.

**Spec:** Polyglot Architecture & Anti-Slop Bento Notification Specification.

## Global Constraints

- Go binary must compile statically for Windows (`GOOS=windows GOARCH=amd64`) without CGO dependencies (`CGO_ENABLED=0`) by using `modernc.org/sqlite`.
- All Python agent logic (LangGraph, Notion sync, Kokoro TTS, Google Calendar) remains untouched and is invoked on-demand via `uv run deskpilot`.
- Memory footprint of the idle background daemon must remain under 12 MB RAM.
- Visual design of any notification must strictly conform to DeskPilot's Bento theme: `#161b22` surface, `#30363d` hairline borders, `#ffea79` headers, `#f69d50` amber primary buttons. No neon glows or developer vanity stats.
- Each task ends with its own independently testable deliverable and a clean, dedicated git commit.

## Review Focus

1. **Path Handling on Windows:** Working directories, Windows user profiles (`%USERPROFILE%`, `%LOCALAPPDATA%`), and quoted path execution for `uv.exe`.
2. **SQLite Concurrency:** Go and Python concurrently opening `storage.db`. Ensure WAL mode (`PRAGMA journal_mode=WAL;`) is active so neither process locks out the other.
3. **Notification Non-Intrusiveness:** Custom notification window must never steal focus from active fullscreen games or productivity apps (`WS_EX_NOACTIVATE`).
4. **Clean Process Termination:** Exiting via the System Tray icon must cleanly close database handles and stop background goroutines without leaving orphan processes.
5. **Cadence Correctness:** Timestamps stored in SQLite must use standard Unix epoch integers or ISO 8601 UTC strings matching `deskpilot.daemon`.

---

## Tasks

### Task 1: Initialize Go Module & Shared SQLite State Reader

**Files:**
- Create: `daemon/go.mod`
- Create: `daemon/internal/db/store.go`
- Create: `daemon/internal/db/store_test.go`

- [ ] **Step 1:** Initialize Go module in `daemon/` using `go mod init deskpilot/daemon`.
- [ ] **Step 2:** Add pure Go SQLite driver `modernc.org/sqlite`.
- [ ] **Step 3:** Implement `store.go` with functions to read and update `desktop_pilot_daemon` state namespace in `storage.db` (matching Python's `sqlite3` table `checkpoints` / KV store).
- [ ] **Step 4:** Write unit test in `store_test.go` verifying that Go reads and updates keys accurately in an isolated SQLite database file.
- [ ] **Step 5:** Run `go test ./internal/db/...` to verify test passes.
- [ ] **Step 6:** Commit: `feat(daemon): initialize go module and pure-go sqlite state store`.

---

### Task 2: Implement File Buffer & Cadence Watcher Core

**Files:**
- Create: `daemon/internal/watcher/screenshots.go`
- Create: `daemon/internal/watcher/downloads.go`
- Create: `daemon/internal/watcher/watcher_test.go`

- [ ] **Step 1:** Implement screenshot buffer scanner in `screenshots.go`: count `.png` and `.jpg` files in the configured screenshots directory, ignoring temporary files.
- [ ] **Step 2:** Implement downloads cadence check in `downloads.go`: compare current Unix time against `downloads_last_trigger_ts` in SQLite to check if 5 days have elapsed.
- [ ] **Step 3:** Write tests in `watcher_test.go` using a temporary directory with mock screenshot files and mock timestamps.
- [ ] **Step 4:** Run `go test ./internal/watcher/...` to verify test passes.
- [ ] **Step 5:** Commit: `feat(daemon): implement screenshot buffer and cadence watcher logic`.

---

### Task 3: Implement Python Worker Dispatcher & Protocol Trigger

**Files:**
- Create: `daemon/internal/runner/dispatcher.go`
- Create: `daemon/internal/runner/dispatcher_test.go`

- [ ] **Step 1:** Implement `dispatcher.go` to locate `uv.exe` and execute `uv run deskpilot --batch` or `deskpilot --tui` non-blockingly.
- [ ] **Step 2:** Ensure child processes run with proper working directory set to project root and handle Windows `CREATE_NO_WINDOW` or Windows Terminal (`wt.exe`) launching for interactive mode.
- [ ] **Step 3:** Add unit tests validating CLI command construction and process launcher flags.
- [ ] **Step 4:** Run `go test ./internal/runner/...` to verify.
- [ ] **Step 5:** Commit: `feat(daemon): add process dispatcher for deskpilot python worker`.

---

### Task 4: Implement Windows Toast Notification Dispatcher

**Files:**
- Create: `daemon/internal/notify/toast.go`
- Create: `daemon/internal/notify/toast_test.go`

- [ ] **Step 1:** Implement native Windows Toast notification dispatch using Windows PowerShell `BurntToast` or native WinRT/PowerShell XML payload with `deskpilot://open` protocol action.
- [ ] **Step 2:** Provide clean fallback if PowerShell execution encounters restrictions.
- [ ] **Step 3:** Write unit test verifying XML payload generation matches Windows 11 notification schema with title, body, and action button.
- [ ] **Step 4:** Commit: `feat(daemon): implement native windows toast notification dispatcher`.

---

### Task 5: Assemble Go Main Daemon & System Tray Integration

**Files:**
- Create: `daemon/main.go`
- Create: `daemon/internal/tray/tray.go`
- Create: `daemon/config.go`

- [ ] **Step 1:** Implement `config.go` to load paths and thresholds from project root's `config.yaml`.
- [ ] **Step 2:** Implement `tray.go` using `getlantern/systray` with menu options:
  - `Open Dashboard [TUI]`
  - `Triage Screenshots Now`
  - `Check Downloads Hygiene`
  - `---`
  - `Exit`
- [ ] **Step 3:** Assemble the main daemon event loop in `daemon/main.go` running the periodic ticker (every 60 seconds).
- [ ] **Step 4:** Build the binary `go build -ldflags "-H=windowsgui -s -w" -o ../deskpilot-daemon.exe .` and verify it runs silently.
- [ ] **Step 5:** Commit: `feat(daemon): assemble complete background daemon with system tray menu`.

---

### Task 6: Implement Phase 2 Custom Bento Notification Card

**Files:**
- Create: `daemon/internal/notify/bento_window.go`

- [ ] **Step 1:** Implement lightweight Win32 popup window using `WS_POPUP | WS_EX_TOPMOST | WS_EX_NOACTIVATE` (or custom GDI renderer).
- [ ] **Step 2:** Apply Bento visual tokens: `#161b22` background, `#30363d` quiet border, `#ffea79` title, `#f69d50` CTA button.
- [ ] **Step 3:** Add auto-dismiss timer (6 seconds) with click-to-launch handler.
- [ ] **Step 4:** Add toggle in `config.yaml`: `notification.style: "bento" | "toast"`.
- [ ] **Step 5:** Commit: `feat(notify): add native bento floating notification card renderer`.

---

### Task 7: Update Windows Scheduled Tasks & Registration Scripts

**Files:**
- Modify: `scripts/register_startup.ps1`
- Modify: `scripts/unregister_startup.ps1`
- Modify: `tests/test_startup_scripts.py`

- [ ] **Step 1:** Update `register_startup.ps1` to register `deskpilot-daemon.exe` directly under `DeskPilotDaemon` instead of wrapping a persistent PowerShell interpreter.
- [ ] **Step 2:** Update `unregister_startup.ps1` to stop and remove `deskpilot-daemon.exe`.
- [ ] **Step 3:** Run PowerShell test suite `uv run pytest tests/test_startup_scripts.py` and verify all tests pass.
- [ ] **Step 4:** Commit: `chore(startup): configure scheduled tasks for native go daemon binary`.
