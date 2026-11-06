package db

import (
	"path/filepath"
	"testing"
	"time"
)

func TestStore_PutAndGet(t *testing.T) {
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "test_storage.db")

	store, err := OpenStore(dbPath)
	if err != nil {
		t.Fatalf("OpenStore failed: %v", err)
	}
	defer store.Close()

	// Initial get on empty key
	item, err := store.GetItem(DaemonNamespace, DownloadsLastTriggerKey)
	if err != nil {
		t.Fatalf("GetItem returned error: %v", err)
	}
	if item != nil {
		t.Fatalf("Expected nil item, got: %v", item)
	}

	// Record timestamp
	if err := store.RecordTimestamp(DownloadsLastTriggerKey); err != nil {
		t.Fatalf("RecordTimestamp failed: %v", err)
	}

	// Retrieve timestamp
	ts, err := store.GetLastTimestamp(DownloadsLastTriggerKey)
	if err != nil {
		t.Fatalf("GetLastTimestamp failed: %v", err)
	}
	now := float64(time.Now().Unix())
	if ts <= 0 || (now-ts) > 5 {
		t.Fatalf("Unexpected timestamp: got %f, current %f", ts, now)
	}
}
