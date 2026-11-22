package watcher

import (
	"os"
	"path/filepath"
	"strings"
	"time"

	"deskpilot/daemon/internal/db"
)

// DefaultScreenshotAlertCooldownSeconds is the default cooldown between screenshot threshold alerts (1 hour).
const DefaultScreenshotAlertCooldownSeconds = 3600

// CountEligibleScreenshots counts valid .png, .jpg, .jpeg, .webp, and .bmp screenshots, ignoring temporary/hidden files.
func CountEligibleScreenshots(dirPath string) ([]string, error) {
	entries, err := os.ReadDir(dirPath)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}

	var screenshots []string
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		name := entry.Name()
		if strings.HasPrefix(name, ".") || strings.HasPrefix(name, "~") {
			continue
		}
		ext := strings.ToLower(filepath.Ext(name))
		if ext == ".png" || ext == ".jpg" || ext == ".jpeg" || ext == ".webp" || ext == ".bmp" {
			screenshots = append(screenshots, filepath.Join(dirPath, name))
		}
	}
	return screenshots, nil
}

// ShouldTriggerScreenshotAlert checks if the screenshot alert cooldown has elapsed.
func ShouldTriggerScreenshotAlert(store *db.Store, cooldownSeconds int) (bool, error) {
	if store == nil {
		return true, nil
	}
	lastTs, err := store.GetLastTimestamp(db.ScreenshotsLastAlertKey)
	if err != nil {
		return false, err
	}
	if lastTs <= 0 {
		return true, nil
	}
	now := float64(time.Now().Unix())
	return (now - lastTs) >= float64(cooldownSeconds), nil
}
