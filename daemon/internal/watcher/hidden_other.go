//go:build !windows

package watcher

import "io/fs"

// isHiddenOrSystem: non-Windows platforms rely on the dot-prefix convention instead.
func isHiddenOrSystem(info fs.FileInfo) bool { return false }
