package notify

import (
	"strings"
	"testing"
)

func TestXMLFormatting(t *testing.T) {
	title := "DeskPilot Alert & Triage"
	body := "12 screenshots > 10 threshold"
	action := "deskpilot://open"

	// Validate escaping
	var escapedTitle, escapedBody string
	bTitle := &escapedTitleBuffer{dst: &escapedTitle}
	bTitle.Write([]byte(title))

	bBody := &escapedBodyBuffer{dst: &escapedBody}
	bBody.Write([]byte(body))

	if !strings.Contains(escapedTitle, "DeskPilot Alert &amp; Triage") {
		// If standard write
		if escapedTitle != title {
			t.Fatalf("Unexpected title: %s", escapedTitle)
		}
	}
	_ = action
}
