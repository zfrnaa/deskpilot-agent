package main

import (
	"fmt"
	"log"
	"os"
	"path/filepath"
	"time"

	"github.com/getlantern/systray"

	"deskpilot/daemon/internal/db"
	"deskpilot/daemon/internal/notify"
	"deskpilot/daemon/internal/runner"
	"deskpilot/daemon/internal/watcher"
)

var (
	projectRoot string
	cfg         *Config
	store       *db.Store
)

func main() {
	// Determine project root directory (parent of daemon/)
	exePath, err := os.Executable()
	if err != nil {
		log.Fatalf("failed to get executable path: %v", err)
	}
	exeDir := filepath.Dir(exePath)

	// If running inside daemon/ or bin/, find project root with config.yaml
	projectRoot = exeDir
	if _, err := os.Stat(filepath.Join(projectRoot, "config.yaml")); err != nil {
		projectRoot = filepath.Dir(exeDir)
	}

	loadedCfg, err := LoadConfig(projectRoot)
	if err != nil {
		log.Printf("Warning: failed to load config.yaml, using defaults: %v", err)
		loadedCfg = &Config{}
		loadedCfg.Daemon.PollIntervalSeconds = 60
		loadedCfg.Daemon.ScreenshotThreshold = 10
		loadedCfg.Daemon.DownloadsCadenceDays = 5
	}
	cfg = loadedCfg

	s, err := db.OpenStore(cfg.Storage.DbPath)
	if err != nil {
		log.Printf("Warning: could not open storage store: %v", err)
	} else {
		store = s
		defer store.Close()
	}

	// Start background watcher loop
	go runWatcherLoop()

	// Initialize system tray icon
	systray.Run(onTrayReady, onTrayExit)
}

func runWatcherLoop() {
	interval := time.Duration(cfg.Daemon.PollIntervalSeconds) * time.Second
	if interval < 10*time.Second {
		interval = 60 * time.Second
	}
	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	// Check on launch once
	checkThresholds()

	for range ticker.C {
		checkThresholds()
	}
}

func checkThresholds() {
	if cfg == nil {
		return
	}

	// 1. Screenshot threshold check
	if cfg.Screenshots.Directory != "" {
		shots, err := watcher.CountEligibleScreenshots(cfg.Screenshots.Directory)
		if err == nil && len(shots) >= cfg.Daemon.ScreenshotThreshold {
			title := "DeskPilot Screenshot Triage"
			body := fmt.Sprintf("%d screenshots pending in buffer. Click to open dashboard.", len(shots))
			_ = notify.SendWindowsToast(title, body, "deskpilot://open")
		}
	}

	// 2. Downloads cadence check
	if store != nil {
		trig, err := watcher.ShouldTriggerDownloads(store, cfg.Daemon.DownloadsCadenceDays)
		if err == nil && trig {
			title := "DeskPilot Downloads Hygiene"
			body := "5-day downloads hygiene cadence reached. Ready to organize files."
			_ = notify.SendWindowsToast(title, body, "deskpilot://open")
		}
	}
}

func onTrayReady() {
	systray.SetTitle("DeskPilot")
	systray.SetTooltip("DeskPilot Autonomous Watcher Daemon")

	mOpen := systray.AddMenuItem("Open Dashboard [TUI]", "Open DeskPilot Interactive Dashboard")
	mTriage := systray.AddMenuItem("Triage Screenshots Now", "Launch Screenshot Agent Triage")
	systray.AddSeparator()
	mExit := systray.AddMenuItem("Exit", "Quit DeskPilot Daemon")

	go func() {
		for {
			select {
			case <-mOpen.ClickedCh:
				_ = runner.RunDeskPilotInteractive(projectRoot, "--tui")
			case <-mTriage.ClickedCh:
				_ = runner.RunDeskPilotInteractive(projectRoot, "--tui")
			case <-mExit.ClickedCh:
				systray.Quit()
				return
			}
		}
	}()
}

func onTrayExit() {
	// Cleanup if necessary
}
