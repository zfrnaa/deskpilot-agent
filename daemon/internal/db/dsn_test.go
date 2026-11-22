package db

import (
	"path/filepath"
	"strings"
	"testing"
)

func TestBuildSQLiteDSN_SpecialCharsAndMemory(t *testing.T) {
	memDSN := buildSQLiteDSN(":memory:")
	if !strings.HasPrefix(memDSN, ":memory:") {
		t.Fatalf("expected :memory: DSN, got %s", memDSN)
	}

	specialDSN := buildSQLiteDSN(`C:\tmp\dir#1\test?v=2.db`)
	if !strings.HasPrefix(specialDSN, "file:") {
		t.Fatalf("expected file: URI prefix, got %s", specialDSN)
	}
	if strings.Contains(specialDSN, "#1") || strings.Contains(specialDSN, "test?v=2") {
		t.Fatalf("expected # and ? to be percent-encoded in path, got %s", specialDSN)
	}
}

func TestOpenStore_MemoryAndHashPath(t *testing.T) {
	memStore, err := OpenStore(":memory:")
	if err != nil {
		t.Fatalf("OpenStore(:memory:) failed: %v", err)
	}
	defer memStore.Close()
	if err := memStore.RecordTimestamp(ScreenshotsLastAlertKey); err != nil {
		t.Fatalf("RecordTimestamp failed on :memory:: %v", err)
	}

	hashDir := filepath.Join(t.TempDir(), "dir#1")
	hashPath := filepath.Join(hashDir, "store.db")
	hashStore, err := OpenStore(hashPath)
	if err != nil {
		t.Fatalf("OpenStore with # in path failed: %v", err)
	}
	defer hashStore.Close()
	if err := hashStore.RecordTimestamp(DownloadsLastTriggerKey); err != nil {
		t.Fatalf("RecordTimestamp failed on hash path: %v", err)
	}
}
