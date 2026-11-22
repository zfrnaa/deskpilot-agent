package runner

import (
	"fmt"
	"strings"
	"syscall"
	"unsafe"
)

var (
	shell32DLL = syscall.NewLazyDLL("shell32.dll")
	procShellExecuteW = shell32DLL.NewProc("ShellExecuteW")
)

const SW_HIDE = 0

// quoteWindowsArg quotes a single command-line argument according to Windows CommandLineToArgvW rules.
func quoteWindowsArg(arg string) string {
	var b strings.Builder
	b.WriteByte('"')
	backslashes := 0
	for i := 0; i < len(arg); i++ {
		c := arg[i]
		if c == '\\' {
			backslashes++
		} else if c == '"' {
			for j := 0; j < backslashes*2+1; j++ {
				b.WriteByte('\\')
			}
			b.WriteByte('"')
			backslashes = 0
		} else {
			for j := 0; j < backslashes; j++ {
				b.WriteByte('\\')
			}
			backslashes = 0
			b.WriteByte(c)
		}
	}
	for j := 0; j < backslashes*2; j++ {
		b.WriteByte('\\')
	}
	b.WriteByte('"')
	return b.String()
}

// RunDeskPilotElevated triggers 'uv run deskpilot <args>' with Administrator privileges using the 'runas' verb.
func RunDeskPilotElevated(workDir string, args ...string) error {
	uv := LocateUV()

	// Arguments for uv
	cmdArgs := append([]string{"run", "--directory", workDir, "deskpilot"}, args...)
	quoted := make([]string, len(cmdArgs))
	for i, a := range cmdArgs {
		quoted[i] = quoteWindowsArg(a)
	}
	argString := strings.Join(quoted, " ")

	verbPtr, _ := syscall.UTF16PtrFromString("runas")
	filePtr, _ := syscall.UTF16PtrFromString(uv)
	argPtr, _ := syscall.UTF16PtrFromString(argString)
	dirPtr, _ := syscall.UTF16PtrFromString(workDir)

	ret, _, _ := procShellExecuteW.Call(
		0,
		uintptr(unsafe.Pointer(verbPtr)),
		uintptr(unsafe.Pointer(filePtr)),
		uintptr(unsafe.Pointer(argPtr)),
		uintptr(unsafe.Pointer(dirPtr)),
		uintptr(SW_HIDE),
	)

	// ShellExecute returns > 32 on success
	if ret <= 32 {
		return fmt.Errorf("ShellExecuteW failed with error code: %d", ret)
	}
	return nil
}
