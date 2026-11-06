package watcher

import (
	"os"
	"path/filepath"
	"strings"
)

// CountEligibleScreenshots counts valid .png, .jpg, and .jpeg screenshots, ignoring temporary files.
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
		if ext == ".png" || ext == ".jpg" || ext == ".jpeg" {
			screenshots = append(screenshots, filepath.Join(dirPath, name))
		}
	}
	return screenshots, nil
}
