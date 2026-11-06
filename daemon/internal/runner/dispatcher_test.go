package runner

import (
	"testing"
)

func TestLocateUV(t *testing.T) {
	uv := LocateUV()
	if uv == "" {
		t.Fatal("LocateUV returned empty string")
	}
}

func TestLocateWindowsTerminal(t *testing.T) {
	// Should not panic, returns either wt path or empty
	_ = LocateWindowsTerminal()
}
