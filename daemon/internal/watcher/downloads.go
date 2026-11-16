package watcher

import (
	"os"
	"strings"
	"time"

	"deskpilot/daemon/internal/db"
)

// CountEligibleDownloads counts non-hidden top-level files in the downloads directory.
func CountEligibleDownloads(dirPath string) (int, error) {
	if dirPath == "" {
		return 0, nil
	}
	entries, err := os.ReadDir(dirPath)
	if err != nil {
		if os.IsNotExist(err) {
			return 0, nil
		}
		return 0, err
	}

	count := 0
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		name := entry.Name()
		if strings.HasPrefix(name, ".") || strings.HasPrefix(name, "~") {
			continue
		}
		count++
	}
	return count, nil
}

// ShouldTriggerDownloads checks if the configured downloads cadence (in days) has elapsed since last triage.
func ShouldTriggerDownloads(store *db.Store, cadenceDays int) (bool, error) {
	lastTs, err := store.GetLastTimestamp(db.DownloadsLastTriggerKey)
	if err != nil {
		return false, err
	}
	if lastTs <= 0 {
		return true, nil
	}

	secondsThreshold := float64(cadenceDays * 86400)
	now := float64(time.Now().Unix())
	return (now - lastTs) >= secondsThreshold, nil
}
