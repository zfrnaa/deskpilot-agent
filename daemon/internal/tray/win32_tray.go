package tray

import (
	"fmt"
	"syscall"
	"unsafe"

	"github.com/lxn/win"
)

const (
	WM_APP             = 0x8000
	WM_TRAYICON        = WM_APP + 1
	CMD_OPEN_DASHBOARD = 1001
	CMD_CLEAR_RAM      = 1002
	CMD_TRIAGE_SHOTS   = 1003
	CMD_EXIT           = 1004
)

type TrayCallbacks struct {
	OnOpenDashboard func()
	OnClearRAM      func()
	OnTriageShots   func()
	OnExit          func()
}

var (
	globalCallbacks TrayCallbacks
	globalNID       win.NOTIFYICONDATA
)

func wndProc(hWnd win.HWND, msg uint32, wParam, lParam uintptr) uintptr {
	switch msg {
	case WM_TRAYICON:
		if lParam == win.WM_RBUTTONUP {
			showContextMenu(hWnd)
		} else if lParam == win.WM_LBUTTONDBLCLK {
			if globalCallbacks.OnOpenDashboard != nil {
				globalCallbacks.OnOpenDashboard()
			}
		}
	case win.WM_COMMAND:
		cmdID := uint32(wParam & 0xffff)
		switch cmdID {
		case CMD_OPEN_DASHBOARD:
			if globalCallbacks.OnOpenDashboard != nil {
				globalCallbacks.OnOpenDashboard()
			}
		case CMD_CLEAR_RAM:
			if globalCallbacks.OnClearRAM != nil {
				globalCallbacks.OnClearRAM()
			}
		case CMD_TRIAGE_SHOTS:
			if globalCallbacks.OnTriageShots != nil {
				globalCallbacks.OnTriageShots()
			}
		case CMD_EXIT:
			win.Shell_NotifyIcon(win.NIM_DELETE, &globalNID)
			win.PostQuitMessage(0)
			if globalCallbacks.OnExit != nil {
				globalCallbacks.OnExit()
			}
		}
	case win.WM_DESTROY:
		win.Shell_NotifyIcon(win.NIM_DELETE, &globalNID)
		win.PostQuitMessage(0)
	default:
		return win.DefWindowProc(hWnd, msg, wParam, lParam)
	}
	return 0
}

func showContextMenu(hWnd win.HWND) {
	hMenu := win.CreatePopupMenu()
	if hMenu == 0 {
		return
	}
	defer win.DestroyMenu(hMenu)

	appendMenuItem(hMenu, 0, CMD_OPEN_DASHBOARD, "Open Dashboard [TUI]")
	appendMenuItem(hMenu, 1, CMD_CLEAR_RAM, "⚡ Clear RAM & Working Sets")
	appendMenuItem(hMenu, 2, CMD_TRIAGE_SHOTS, "📸 Triage Screenshots Now")
	appendSeparator(hMenu, 3)
	appendMenuItem(hMenu, 4, CMD_EXIT, "Exit")

	var pt win.POINT
	win.GetCursorPos(&pt)

	win.SetForegroundWindow(hWnd)
	win.TrackPopupMenu(
		hMenu,
		win.TPM_BOTTOMALIGN|win.TPM_LEFTALIGN,
		pt.X,
		pt.Y,
		0,
		hWnd,
		nil,
	)
	win.PostMessage(hWnd, 0, 0, 0)
}

func appendMenuItem(hMenu win.HMENU, pos uint32, id uint32, text string) {
	pStr, _ := syscall.UTF16PtrFromString(text)
	mii := win.MENUITEMINFO{
		FMask:      win.MIIM_STRING | win.MIIM_ID | win.MIIM_STATE,
		FType:      win.MFT_STRING,
		FState:     win.MFS_ENABLED,
		WID:        id,
		DwTypeData: pStr,
	}
	mii.CbSize = uint32(unsafe.Sizeof(mii))
	win.InsertMenuItem(hMenu, pos, true, &mii)
}

func appendSeparator(hMenu win.HMENU, pos uint32) {
	mii := win.MENUITEMINFO{
		FMask: win.MIIM_FTYPE,
		FType: win.MFT_SEPARATOR,
	}
	mii.CbSize = uint32(unsafe.Sizeof(mii))
	win.InsertMenuItem(hMenu, pos, true, &mii)
}

// RunTray starts the native Win32 system tray icon.
func RunTray(tooltip string, callbacks TrayCallbacks) error {
	globalCallbacks = callbacks

	className, _ := syscall.UTF16PtrFromString("DeskPilotTrayClass")
	hInstance := win.GetModuleHandle(nil)
	hIcon := win.LoadIcon(0, win.MAKEINTRESOURCE(win.IDI_APPLICATION))

	wcx := win.WNDCLASSEX{
		CbSize:        uint32(unsafe.Sizeof(win.WNDCLASSEX{})),
		LpfnWndProc:   syscall.NewCallback(wndProc),
		HInstance:     hInstance,
		HIcon:         hIcon,
		LpszClassName: className,
	}

	win.RegisterClassEx(&wcx)

	hWnd := win.CreateWindowEx(
		0,
		className,
		className,
		win.WS_OVERLAPPEDWINDOW,
		win.CW_USEDEFAULT, win.CW_USEDEFAULT, win.CW_USEDEFAULT, win.CW_USEDEFAULT,
		0, 0, hInstance, nil,
	)
	if hWnd == 0 {
		return fmt.Errorf("CreateWindowEx failed: %d", win.GetLastError())
	}

	globalNID = win.NOTIFYICONDATA{
		HWnd:             hWnd,
		UID:              1,
		UFlags:           win.NIF_MESSAGE | win.NIF_ICON | win.NIF_TIP,
		UCallbackMessage: WM_TRAYICON,
		HIcon:            hIcon,
	}
	globalNID.CbSize = uint32(unsafe.Sizeof(globalNID))

	tipChars, _ := syscall.UTF16FromString(tooltip)
	copy(globalNID.SzTip[:], tipChars)

	if !win.Shell_NotifyIcon(win.NIM_ADD, &globalNID) {
		return fmt.Errorf("Shell_NotifyIcon failed: %d", win.GetLastError())
	}

	var msg win.MSG
	for win.GetMessage(&msg, 0, 0, 0) > 0 {
		win.TranslateMessage(&msg)
		win.DispatchMessage(&msg)
	}

	return nil
}
