package watcher

import (
	"syscall"
	"time"
	"unsafe"

	"deskpilot/daemon/internal/db"
)

var (
	modkernel32              = syscall.NewLazyDLL("kernel32.dll")
	procGlobalMemoryStatusEx = modkernel32.NewProc("GlobalMemoryStatusEx")
)

type MEMORYSTATUSEX struct {
	CbSize                  uint32
	DwMemoryLoad            uint32
	UllTotalPhys            uint64
	UllAvailPhys            uint64
	UllTotalPageFile        uint64
	UllAvailPageFile        uint64
	UllTotalVirtual         uint64
	UllAvailVirtual         uint64
	UllAvailExtendedVirtual uint64
}

// GetMemoryLoadPercent returns the current physical RAM load percentage (e.g. 75 for 75%).
func GetMemoryLoadPercent() int {
	var stat MEMORYSTATUSEX
	stat.CbSize = uint32(unsafe.Sizeof(stat))
	r, _, _ := procGlobalMemoryStatusEx.Call(uintptr(unsafe.Pointer(&stat)))
	if r == 0 {
		return 0
	}
	return int(stat.DwMemoryLoad)
}

// ShouldTriggerMemoryCleanup checks if periodic memory cleanup is due (interval or high RAM load).
func ShouldTriggerMemoryCleanup(store *db.Store, intervalMinutes int, loadThresholdPercent int) (bool, error) {
	// 1. High load check (e.g. >= 75%)
	load := GetMemoryLoadPercent()
	if load >= loadThresholdPercent && loadThresholdPercent > 0 {
		return true, nil
	}

	// 2. Time cadence check
	lastTs, err := store.GetLastTimestamp(db.MemoryCleanupLastTriggerKey)
	if err != nil {
		return false, err
	}
	if lastTs <= 0 {
		return true, nil
	}

	secondsThreshold := float64(intervalMinutes * 60)
	now := float64(time.Now().Unix())
	return (now - lastTs) >= secondsThreshold, nil
}
