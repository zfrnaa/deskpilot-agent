package main

import (
	"fmt"
	"log"
	"os"
	"path/filepath"
	"time"

	"deskpilot/daemon/internal/db"
	"deskpilot/daemon/internal/notify"
	"deskpilot/daemon/internal/runner"
	"deskpilot/daemon/internal/tray"
	"deskpilot/daemon/internal/watcher"
)

var (
	projectRoot string
	cfg         *Config
	store       *db.Store
)

func findProjectRoot() string {
	cwd, err := os.Getwd()
	if err == nil {
		if _, err := os.Stat(filepath.Join(cwd, "config.yaml")); err == nil {
			return cwd
		}
	}

	exePath, err := os.Executable()
	if err == nil {
		exeDir := filepath.Dir(exePath)
		if _, err := os.Stat(filepath.Join(exeDir, "config.yaml")); err == nil {
			return exeDir
		}
		parent := filepath.Dir(exeDir)
		if _, err := os.Stat(filepath.Join(parent, "config.yaml")); err == nil {
			return parent
		}
	}
	return "."
}

func main() {
	projectRoot = findProjectRoot()

	loadedCfg, err := LoadConfig(projectRoot)
	if err != nil {
		log.Printf("Warning: failed to load config.yaml: %v", err)
		loadedCfg = &Config{}
		loadedCfg.Daemon.PollIntervalSeconds = 60
		loadedCfg.Daemon.ScreenshotThreshold = 10
		loadedCfg.Daemon.DownloadsCadenceDays = 5
	}
	cfg = loadedCfg

	s, err := db.OpenStore(cfg.Storage.DbPath)
	if err != nil {
		log.Printf("Warning: could not open storage store at %s: %v", cfg.Storage.DbPath, err)
	} else {
		store = s
		defer store.Close()
	}

	// Start background watcher loop in goroutine
	go runWatcherLoop()

	// Launch native Win32 system tray
	callbacks := tray.TrayCallbacks{
		OnOpenDashboard: func() {
			_ = runner.RunDeskPilotInteractive(projectRoot, "--tui")
		},
		OnClearRAM: func() {
			// Trigger Python memory optimization pipeline with elevated privileges
			go func() {
				err := runner.RunDeskPilotElevated(projectRoot, "--clear-ram")
				if err != nil {
					// Fallback to silent non-elevated if UAC is canceled
					_ = runner.RunDeskPilotSilent(projectRoot, "--clear-ram")
				}
				_ = notify.ShowBentoNotification("Memory Optimization", "Standby list purged and process working sets trimmed.", "")
			}()
		},
		OnTriageShots: func() {
			_ = runner.RunDeskPilotInteractive(projectRoot, "--tui")
		},
		OnExit: func() {
			os.Exit(0)
		},
	}

	if err := tray.RunTray("DeskPilot Autonomous Daemon", callbacks); err != nil {
		log.Printf("Tray failed: %v", err)
		// Fallback: stay alive in background loop if tray fails
		select {}
	}
}

func runWatcherLoop() {
	interval := time.Duration(cfg.Daemon.PollIntervalSeconds) * time.Second
	if interval < 5*time.Second {
		interval = 60 * time.Second
	}
	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	// Initial check on boot
	checkThresholds()

	for range ticker.C {
		checkThresholds()
	}
}

func dispatchAlert(title, body, actionCommand string) {
	if cfg != nil && cfg.Daemon.NotificationStyle == "toast" {
		if err := notify.SendWindowsToast(title, body, actionCommand); err != nil {
			log.Printf("SendWindowsToast error: %v", err)
		}
	} else {
		if err := notify.ShowBentoNotification(title, body, actionCommand); err != nil {
			log.Printf("ShowBentoNotification error: %v", err)
			_ = notify.SendWindowsToast(title, body, actionCommand)
		}
	}
}

func checkThresholds() {
	if cfg == nil {
		return
	}

	// 1. Screenshot threshold check
	if cfg.Screenshots.Directory != "" {
		shots, err := watcher.CountEligibleScreenshots(cfg.Screenshots.Directory)
		if err != nil {
			log.Printf("watcher error on %s: %v", cfg.Screenshots.Directory, err)
		} else if len(shots) >= cfg.Daemon.ScreenshotThreshold {
			title := "Screenshot Triage Alert"
			body := fmt.Sprintf("%d screenshots accumulated in buffer. Ready to classify and archive to Notion.", len(shots))
			dispatchAlert(title, body, "deskpilot://open")
		}
	}

	// 2. Downloads cadence check
	if store != nil {
		trig, err := watcher.ShouldTriggerDownloads(store, cfg.Daemon.DownloadsCadenceDays)
		if err == nil && trig {
			title := "Downloads Hygiene Alert"
			body := "5-day downloads hygiene cadence reached. Ready to organize files."
			dispatchAlert(title, body, "deskpilot://open")
		}
	}

	// 3. Periodic memory optimization check
	if store != nil && cfg.Memory.Enabled && cfg.Memory.PeriodicCleanupEnabled {
		trig, err := watcher.ShouldTriggerMemoryCleanup(
			store,
			cfg.Memory.PeriodicIntervalMinutes,
			cfg.Memory.HighMemoryLoadThresholdPercent,
		)
		if err == nil && trig {
			// Trigger silent working sets trimming & record timestamp in SQLite
			_ = runner.RunDeskPilotSilent(projectRoot, "--clear-ram")
			_ = store.RecordTimestamp(db.MemoryCleanupLastTriggerKey)
		}
	}
}
