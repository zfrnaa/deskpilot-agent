package watcher

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"deskpilot/daemon/internal/db"
)

func TestCountEligibleScreenshots(t *testing.T) {
	tmpDir := t.TempDir()

	// Create dummy files
	os.WriteFile(filepath.Join(tmpDir, "shot1.png"), []byte("png"), 0644)
	os.WriteFile(filepath.Join(tmpDir, "shot2.JPG"), []byte("jpg"), 0644)
	os.WriteFile(filepath.Join(tmpDir, ".hidden.png"), []byte("hidden"), 0644)
	os.WriteFile(filepath.Join(tmpDir, "doc.pdf"), []byte("pdf"), 0644)
	os.Mkdir(filepath.Join(tmpDir, "subfolder"), 0755)

	files, err := CountEligibleScreenshots(tmpDir)
	if err != nil {
		t.Fatalf("CountEligibleScreenshots returned error: %v", err)
	}

	if len(files) != 2 {
		t.Fatalf("Expected 2 screenshots, got %d (%v)", len(files), files)
	}
}

func TestShouldTriggerDownloads(t *testing.T) {
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "test.db")
	store, err := db.OpenStore(dbPath)
	if err != nil {
		t.Fatalf("OpenStore error: %v", err)
	}
	defer store.Close()

	// Fresh store: should trigger
	trig, err := ShouldTriggerDownloads(store, 5)
	if err != nil || !trig {
		t.Fatalf("Expected true for empty store, got trig=%v err=%v", trig, err)
	}

	// Record timestamp now: should not trigger
	store.RecordTimestamp(db.DownloadsLastTriggerKey)
	trig, err = ShouldTriggerDownloads(store, 5)
	if err != nil || trig {
		t.Fatalf("Expected false immediately after recording, got trig=%v err=%v", trig, err)
	}

	// Manually inject 6-day old timestamp
	oldTs := float64(time.Now().Add(-6 * 24 * time.Hour).Unix())
	store.PutItem(db.DaemonNamespace, db.DownloadsLastTriggerKey, map[string]any{"timestamp": oldTs})
	trig, err = ShouldTriggerDownloads(store, 5)
	if err != nil || !trig {
		t.Fatalf("Expected true after 6 days, got trig=%v err=%v", trig, err)
	}
}
