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
	os.WriteFile(filepath.Join(tmpDir, "shot3.webp"), []byte("webp"), 0644)
	os.WriteFile(filepath.Join(tmpDir, "shot4.bmp"), []byte("bmp"), 0644)
	os.WriteFile(filepath.Join(tmpDir, ".hidden.png"), []byte("hidden"), 0644)
	os.WriteFile(filepath.Join(tmpDir, "~temp.png"), []byte("temp"), 0644)
	os.WriteFile(filepath.Join(tmpDir, "doc.pdf"), []byte("pdf"), 0644)
	os.Mkdir(filepath.Join(tmpDir, "subfolder"), 0755)

	files, err := CountEligibleScreenshots(tmpDir)
	if err != nil {
		t.Fatalf("CountEligibleScreenshots returned error: %v", err)
	}

	if len(files) != 4 {
		t.Fatalf("Expected 4 screenshots, got %d (%v)", len(files), files)
	}
}

func TestShouldTriggerScreenshotAlert(t *testing.T) {
	store, err := db.OpenStore(filepath.Join(t.TempDir(), "shots.db"))
	if err != nil {
		t.Fatalf("OpenStore error: %v", err)
	}
	defer store.Close()

	// Fresh store: should trigger
	trig, err := ShouldTriggerScreenshotAlert(store, 3600)
	if err != nil || !trig {
		t.Fatalf("Expected true for fresh store, got trig=%v err=%v", trig, err)
	}

	// Immediately after recording: should not trigger
	_ = store.RecordTimestamp(db.ScreenshotsLastAlertKey)
	trig, err = ShouldTriggerScreenshotAlert(store, 3600)
	if err != nil || trig {
		t.Fatalf("Expected false within cooldown, got trig=%v err=%v", trig, err)
	}

	// After cooldown elapses: should trigger
	oldTs := float64(time.Now().Add(-2 * time.Hour).Unix())
	_ = store.PutItem(db.DaemonNamespace, db.ScreenshotsLastAlertKey, map[string]any{"timestamp": oldTs})
	trig, err = ShouldTriggerScreenshotAlert(store, 3600)
	if err != nil || !trig {
		t.Fatalf("Expected true after cooldown, got trig=%v err=%v", trig, err)
	}
}

func TestShouldTriggerMemoryCleanup_HighLoadCooldown(t *testing.T) {
	store, err := db.OpenStore(filepath.Join(t.TempDir(), "mem.db"))
	if err != nil {
		t.Fatalf("OpenStore error: %v", err)
	}
	defer store.Close()

	origFn := getMemoryLoadPercentFunc
	defer func() { getMemoryLoadPercentFunc = origFn }()
	getMemoryLoadPercentFunc = func() int { return 90 }

	// Fresh store: should trigger
	trig, err := ShouldTriggerMemoryCleanup(store, 30, 75)
	if err != nil || !trig {
		t.Fatalf("Expected true on fresh store, got trig=%v err=%v", trig, err)
	}

	// Immediately after recording: suppressed by high-memory cooldown
	_ = store.RecordTimestamp(db.MemoryCleanupLastTriggerKey)
	trig, err = ShouldTriggerMemoryCleanup(store, 30, 75)
	if err != nil || trig {
		t.Fatalf("Expected false within high-memory cooldown, got trig=%v err=%v", trig, err)
	}

	// After 6 minutes (> 5-minute high-memory cooldown): should trigger
	sixMinAgo := float64(time.Now().Add(-6 * time.Minute).Unix())
	_ = store.PutItem(db.DaemonNamespace, db.MemoryCleanupLastTriggerKey, map[string]any{"timestamp": sixMinAgo})
	trig, err = ShouldTriggerMemoryCleanup(store, 30, 75)
	if err != nil || !trig {
		t.Fatalf("Expected true after high-memory cooldown elapsed, got trig=%v err=%v", trig, err)
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

func TestCountEligibleDownloads(t *testing.T) {
	tmpDir := t.TempDir()

	count, err := CountEligibleDownloads(tmpDir)
	if err != nil || count != 0 {
		t.Fatalf("Expected 0 files in empty directory, got %d, err: %v", count, err)
	}

	os.WriteFile(filepath.Join(tmpDir, "file1.zip"), []byte("zip"), 0644)
	os.WriteFile(filepath.Join(tmpDir, "installer.exe"), []byte("exe"), 0644)
	os.WriteFile(filepath.Join(tmpDir, ".hidden"), []byte("hidden"), 0644)
	os.Mkdir(filepath.Join(tmpDir, "Archive"), 0755)

	count, err = CountEligibleDownloads(tmpDir)
	if err != nil || count != 2 {
		t.Fatalf("Expected 2 eligible files, got %d, err: %v", count, err)
	}
}
