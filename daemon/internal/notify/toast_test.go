package notify

import (
	"strings"
	"testing"
)

func TestXMLFormatting(t *testing.T) {
	title := "DeskPilot Alert & Triage"
	body := "12 screenshots > 10 threshold"
	action := "deskpilot://open"

	xmlStr := buildToastXML(title, body, action)
	if !strings.Contains(xmlStr, "DeskPilot Alert &amp; Triage") {
		t.Fatalf("Expected escaped title in XML, got: %s", xmlStr)
	}
	if !strings.Contains(xmlStr, "12 screenshots &gt; 10 threshold") {
		t.Fatalf("Expected escaped body in XML, got: %s", xmlStr)
	}
	if !strings.Contains(xmlStr, `launch="deskpilot://open"`) {
		t.Fatalf("Expected launch attribute in XML, got: %s", xmlStr)
	}
}
