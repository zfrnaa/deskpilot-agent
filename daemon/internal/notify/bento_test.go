package notify

import (
	"strings"
	"testing"
)

func TestBuildBentoHTA_EscapesActionCommand(t *testing.T) {
	malicious := `deskpilot://open?a=" + alert(1) + "&b=\test` + "\n</script>"
	hta := buildBentoHTA("Title <1>", "Body & more", malicious)

	if strings.Contains(hta, `shell.Run("deskpilot://open?a="`) {
		t.Fatalf("actionCommand was not escaped in JS string")
	}
	if strings.Count(hta, "</script>") != 1 {
		t.Fatalf("unexpected extra </script> tag leaked into HTA")
	}
	if !strings.Contains(hta, `\u003c/script\u003e`) {
		t.Fatalf("expected escaped script tag in JS string")
	}
	if !strings.Contains(hta, `class="btn-open"`) {
		t.Fatalf("expected open button when actionCommand is non-empty")
	}
}

func TestBuildBentoHTA_EmptyActionCommand(t *testing.T) {
	hta := buildBentoHTA("Memory", "Done", "")

	if strings.Contains(hta, `class="btn-open"`) {
		t.Fatalf("expected open button to be omitted when actionCommand is empty")
	}
	if !strings.Contains(hta, `if (cmd)`) {
		t.Fatalf("expected JS guard for empty actionCommand")
	}
}

func TestBuildToastXML_EscapesActionCommand(t *testing.T) {
	cmd := `deskpilot://open?x="1"&y=<2>`
	xmlStr := buildToastXML("Title", "Body", cmd)

	if strings.Contains(xmlStr, `launch="deskpilot://open?x="1"`) {
		t.Fatalf("unescaped quotes in launch attribute: %s", xmlStr)
	}
	if !strings.Contains(xmlStr, `launch="deskpilot://open?x=&#34;1&#34;&amp;y=&lt;2&gt;"`) {
		t.Fatalf("expected XML-escaped launch attribute, got: %s", xmlStr)
	}
	if !strings.Contains(xmlStr, `arguments="deskpilot://open?x=&#34;1&#34;&amp;y=&lt;2&gt;"`) {
		t.Fatalf("expected XML-escaped arguments attribute, got: %s", xmlStr)
	}
}
