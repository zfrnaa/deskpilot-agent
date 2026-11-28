//go:build windows

package watcher

import (
	"os"
	"path/filepath"
	"syscall"
	"testing"
)

func TestCountEligibleDownloadsSkipsHiddenAttribute(t *testing.T) {
	tmpDir := t.TempDir()
	hidden := filepath.Join(tmpDir, "secret.pdf")
	os.WriteFile(hidden, []byte("x"), 0644)
	os.WriteFile(filepath.Join(tmpDir, "visible.pdf"), []byte("x"), 0644)

	p, _ := syscall.UTF16PtrFromString(hidden)
	if err := syscall.SetFileAttributes(p, syscall.FILE_ATTRIBUTE_HIDDEN); err != nil {
		t.Fatalf("SetFileAttributes: %v", err)
	}

	count, err := CountEligibleDownloads(tmpDir, 0)
	if err != nil || count != 1 {
		t.Fatalf("Expected 1 eligible file (hidden skipped), got %d, err: %v", count, err)
	}
}
