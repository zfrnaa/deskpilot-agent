package runner

import (
	"fmt"
	"syscall"
	"unsafe"
)

var (
	shell32DLL = syscall.NewLazyDLL("shell32.dll")
	procShellExecuteW = shell32DLL.NewProc("ShellExecuteW")
)

const SW_HIDE = 0

// RunDeskPilotElevated triggers 'uv run deskpilot <args>' with Administrator privileges using the 'runas' verb.
func RunDeskPilotElevated(workDir string, args ...string) error {
	uv := LocateUV()
	
	// Arguments for uv
	cmdArgs := append([]string{"run", "--directory", workDir, "deskpilot"}, args...)
	argString := ""
	for i, a := range cmdArgs {
		if i > 0 {
			argString += " "
		}
		argString += fmt.Sprintf("\"%s\"", a)
	}

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
