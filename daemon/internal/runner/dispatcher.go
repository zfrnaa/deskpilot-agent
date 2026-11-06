package runner

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
)

// LocateUV attempts to find the 'uv' executable.
func LocateUV() string {
	if p, err := exec.LookPath("uv"); err == nil {
		return p
	}
	userProfile := os.Getenv("USERPROFILE")
	if userProfile != "" {
		candidates := []string{
			filepath.Join(userProfile, ".local", "bin", "uv.exe"),
			filepath.Join(userProfile, ".cargo", "bin", "uv.exe"),
			filepath.Join(userProfile, "AppData", "Local", "hermes", "bin", "uv.exe"),
		}
		for _, c := range candidates {
			if _, err := os.Stat(c); err == nil {
				return c
			}
		}
	}
	return "uv"
}

// LocateWindowsTerminal checks if wt.exe is available.
func LocateWindowsTerminal() string {
	if p, err := exec.LookPath("wt.exe"); err == nil {
		return p
	}
	localApp := os.Getenv("LOCALAPPDATA")
	if localApp != "" {
		candidate := filepath.Join(localApp, "Microsoft", "WindowsApps", "wt.exe")
		if _, err := os.Stat(candidate); err == nil {
			return candidate
		}
	}
	return ""
}

// RunDeskPilotSilent launches 'uv run deskpilot <args>' in a hidden non-blocking process.
func RunDeskPilotSilent(workDir string, args ...string) error {
	uv := LocateUV()
	cmdArgs := append([]string{"run", "--directory", workDir, "deskpilot"}, args...)
	cmd := exec.Command(uv, cmdArgs...)
	cmd.Dir = workDir
	cmd.SysProcAttr = &syscall.SysProcAttr{
		CreationFlags: 0x08000000, // CREATE_NO_WINDOW
		HideWindow:    true,
	}
	return cmd.Start()
}

// RunDeskPilotInteractive launches DeskPilot inside Windows Terminal (or console) for user interaction.
func RunDeskPilotInteractive(workDir string, args ...string) error {
	wt := LocateWindowsTerminal()
	uv := LocateUV()

	var cmd *exec.Cmd
	if wt != "" {
		// wt.exe -w 0 nt -d "<workDir>" "<uv>" run --directory "<workDir>" deskpilot <args>
		wtArgs := []string{"-w", "0", "nt", "-d", workDir, uv, "run", "--directory", workDir, "deskpilot"}
		wtArgs = append(wtArgs, args...)
		cmd = exec.Command(wt, wtArgs...)
	} else {
		cmdArgs := append([]string{"run", "--directory", workDir, "deskpilot"}, args...)
		cmd = exec.Command(uv, cmdArgs...)
	}
	cmd.Dir = workDir
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("failed to start interactive deskpilot: %w", err)
	}
	return nil
}
