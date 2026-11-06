package watcher

import (
	"time"

	"deskpilot/daemon/internal/db"
)

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
