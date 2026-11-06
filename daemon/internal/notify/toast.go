package notify

import (
	"encoding/xml"
	"fmt"
	"os/exec"
	"syscall"
)

// SendWindowsToast sends a native Windows Toast notification using PowerShell.
func SendWindowsToast(title, body, actionCommand string) error {
	var escapedTitle, escapedBody string
	xml.EscapeText(&escapedTitleBuffer{dst: &escapedTitle}, []byte(title))
	xml.EscapeText(&escapedBodyBuffer{dst: &escapedBody}, []byte(body))

	actionElem := ""
	if actionCommand != "" {
		actionElem = fmt.Sprintf(`<action activationType="protocol" arguments="%s" content="Open Dashboard"/>`, actionCommand)
	}

	xmlPayload := fmt.Sprintf(`<toast launch="%s"><visual><binding template="ToastGeneric"><text>%s</text><text>%s</text></binding></visual><actions>%s<action activationType="system" arguments="dismiss" content="Dismiss"/></actions></toast>`, actionCommand, escapedTitle, escapedBody, actionElem)

	psScript := fmt.Sprintf(`
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$template = @'
%s
'@
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('DeskPilot').Show($toast)
`, xmlPayload)

	cmd := exec.Command("powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", psScript)
	cmd.SysProcAttr = &syscall.SysProcAttr{
		CreationFlags: 0x08000000, // CREATE_NO_WINDOW
		HideWindow:    true,
	}
	return cmd.Run()
}

type escapedTitleBuffer struct{ dst *string }

func (b *escapedTitleBuffer) Write(p []byte) (n int, err error) {
	*b.dst += string(p)
	return len(p), nil
}

type escapedBodyBuffer struct{ dst *string }

func (b *escapedBodyBuffer) Write(p []byte) (n int, err error) {
	*b.dst += string(p)
	return len(p), nil
}
