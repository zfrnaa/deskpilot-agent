package watcher

import (
	"os"
	"path/filepath"
	"strings"
	"time"

	"deskpilot/daemon/internal/db"
)

// DownloadsMinFileAge is how long a file must sit untouched before it is considered
// ready to sort. Mirrors MIN_FILE_AGE_MINUTES / DOWNLOADS_MIN_FILE_AGE_SECONDS in Python.
const DownloadsMinFileAge = 10 * time.Minute

// partialDownloadExts are in-progress browser/download-manager files that are never sorted.
var partialDownloadExts = map[string]bool{
	".crdownload": true, ".part": true, ".partial": true,
	".download": true, ".opdownload": true, ".tmp": true,
}

// CountEligibleDownloads counts top-level files in the downloads directory that are ready to sort:
// non-hidden, not partial downloads, and unmodified for at least minAge.
func CountEligibleDownloads(dirPath string, minAge time.Duration) (int, error) {
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

	now := time.Now()
	count := 0
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		name := entry.Name()
		if strings.HasPrefix(name, ".") || strings.HasPrefix(name, "~") {
			continue
		}
		if lower := strings.ToLower(name); lower == "desktop.ini" || lower == "thumbs.db" {
			continue
		}
		if partialDownloadExts[strings.ToLower(filepath.Ext(name))] {
			continue
		}
		info, err := entry.Info()
		if err != nil || isHiddenOrSystem(info) || now.Sub(info.ModTime()) < minAge {
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
