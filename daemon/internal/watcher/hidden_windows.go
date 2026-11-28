//go:build windows

package watcher

import (
	"io/fs"
	"syscall"
)

// isHiddenOrSystem reports whether Windows marks the file HIDDEN or SYSTEM (e.g. desktop.ini).
func isHiddenOrSystem(info fs.FileInfo) bool {
	data, ok := info.Sys().(*syscall.Win32FileAttributeData)
	if !ok {
		return false
	}
	return data.FileAttributes&(syscall.FILE_ATTRIBUTE_HIDDEN|syscall.FILE_ATTRIBUTE_SYSTEM) != 0
}
