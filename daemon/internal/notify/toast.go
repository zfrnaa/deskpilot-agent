package notify

import (
	"encoding/xml"
	"fmt"
	"os/exec"
	"strings"
	"syscall"
)

// SendWindowsToast sends a native Windows Toast notification using PowerShell.
func SendWindowsToast(title, body, actionCommand string) error {
	xmlPayload := buildToastXML(title, body, actionCommand)

	// Escape single quotes for PowerShell string literal
	psXML := strings.ReplaceAll(xmlPayload, "'", "''")

	psScript := fmt.Sprintf(`
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml('%s')
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('DeskPilot').Show($toast)
`, psXML)

	cmd := exec.Command("powershell", "-NoProfile", "-NonInteractive", "-Command", psScript)
	cmd.SysProcAttr = &syscall.SysProcAttr{
		CreationFlags: 0x08000000, // CREATE_NO_WINDOW
		HideWindow:    true,
	}
	return cmd.Run()
}

func buildToastXML(title, body, actionCommand string) string {
	escapedTitle := escapeXML(title)
	escapedBody := escapeXML(body)

	activation := ""
	actionElem := ""
	if actionCommand != "" {
		escapedAction := escapeXML(actionCommand)
		activation = fmt.Sprintf(`launch="%s" activationType="protocol"`, escapedAction)
		actionElem = fmt.Sprintf(`<actions><action activationType="protocol" arguments="%s" content="Open Dashboard"/></actions>`, escapedAction)
	}

	return fmt.Sprintf(`<toast %s><visual><binding template="ToastGeneric"><text>%s</text><text>%s</text></binding></visual>%s</toast>`, activation, escapedTitle, escapedBody, actionElem)
}

func escapeXML(s string) string {
	var b strings.Builder
	_ = xml.EscapeText(&b, []byte(s))
	return b.String()
}
