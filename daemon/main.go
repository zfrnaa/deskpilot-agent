package main

import (
	"fmt"
	"log"
	"os"
	"path/filepath"
	"time"

	"deskpilot/daemon/internal/db"
	"deskpilot/daemon/internal/notify"
	"deskpilot/daemon/internal/watcher"
)

var (
	projectRoot string
	cfg         *Config
	store       *db.Store
)

func findProjectRoot() string {
	// First check current working directory
	cwd, err := os.Getwd()
	if err == nil {
		if _, err := os.Stat(filepath.Join(cwd, "config.yaml")); err == nil {
			return cwd
		}
	}

	// Then check directory containing the executable
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

	log.Printf("DeskPilot watcher daemon active (watching: %s, threshold: %d)", cfg.Screenshots.Directory, cfg.Daemon.ScreenshotThreshold)

	// Run periodic watcher
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
			title := "DeskPilot Screenshot Triage"
			body := fmt.Sprintf("%d screenshots pending in buffer. Click to open dashboard.", len(shots))
			if err := notify.SendWindowsToast(title, body, "deskpilot://open"); err != nil {
				log.Printf("SendWindowsToast error: %v", err)
			}
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
