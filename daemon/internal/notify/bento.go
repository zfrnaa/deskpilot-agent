package notify

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"time"
)

// ShowBentoNotification displays a native floating Bento-styled popup card in the bottom-right corner.
func ShowBentoNotification(title, body, actionCommand string) error {
	// HTML template matching the approved Bento / Catppuccin design tokens
	htaHTML := fmt.Sprintf(`<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>DeskPilot Alert</title>
<hta:application 
    id="deskpilotToast" 
    applicationname="DeskPilotToast" 
    border="none" 
    caption="no" 
    showintaskbar="no" 
    singleinstance="yes" 
    sysmenu="no" 
    windowstate="normal" 
    scroll="no"
/>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI Variable Text', 'Segoe UI', -apple-system, sans-serif; user-select: none; }
  html, body {
    width: 320px;
    height: 160px;
    background: transparent;
    overflow: hidden;
  }
  .bento-card {
    width: 316px;
    height: 154px;
    margin: 2px;
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 12px 14px;
    color: #c9d1d9;
    box-shadow: 0 10px 25px rgba(0,0,0,0.8);
  }
  .header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 6px;
  }
  .brand {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.5px;
    color: #f69d50;
    font-family: 'JetBrains Mono', Consolas, monospace;
    text-transform: uppercase;
  }
  .time-tag {
    font-size: 10px;
    color: #8b949e;
    font-family: 'JetBrains Mono', Consolas, monospace;
  }
  .title {
    font-size: 13px;
    font-weight: 600;
    color: #ffea79;
    margin-bottom: 4px;
  }
  .body-text {
    font-size: 11px;
    line-height: 1.4;
    color: #8b949e;
    margin-bottom: 12px;
    height: 34px;
    overflow: hidden;
  }
  .actions {
    display: flex;
    gap: 8px;
  }
  .btn-open {
    flex: 1;
    background: #f69d50;
    color: #0d1117;
    font-weight: 700;
    font-size: 11px;
    border: 1px solid rgba(255,255,255,0.1);
    padding: 6px 10px;
    border-radius: 5px;
    cursor: pointer;
    font-family: 'JetBrains Mono', Consolas, monospace;
  }
  .btn-dismiss {
    background: transparent;
    color: #8b949e;
    font-size: 11px;
    border: 1px solid #30363d;
    padding: 6px 12px;
    border-radius: 5px;
    cursor: pointer;
    font-family: 'JetBrains Mono', Consolas, monospace;
  }
</style>
<script>
  var timer = null;
  function init() {
    var width = 320;
    var height = 160;
    window.resizeTo(width, height);
    var x = window.screen.availWidth - width - 16;
    var y = window.screen.availHeight - height - 16;
    window.moveTo(x, y);
    // Auto-dismiss after 8 seconds
    timer = setTimeout(function() { window.close(); }, 8000);
  }
  function openDashboard() {
    clearTimeout(timer);
    var shell = new ActiveXObject("WScript.Shell");
    shell.Run("%s");
    window.close();
  }
  function dismiss() {
    clearTimeout(timer);
    window.close();
  }
</script>
</head>
<body onload="init()">
  <div class="bento-card">
    <div class="header">
      <div class="brand">DESKPILOT</div>
      <div class="time-tag">just now</div>
    </div>
    <div class="title">%s</div>
    <div class="body-text">%s</div>
    <div class="actions">
      <button class="btn-open" onclick="openDashboard()">Open Dashboard [1]</button>
      <button class="btn-dismiss" onclick="dismiss()">Dismiss</button>
    </div>
  </div>
</body>
</html>`, actionCommand, escapeHTML(title), escapeHTML(body))

	tmpFile := filepath.Join(os.TempDir(), fmt.Sprintf("deskpilot_toast_%d.hta", time.Now().UnixNano()))
	if err := os.WriteFile(tmpFile, []byte(htaHTML), 0644); err != nil {
		return err
	}

	cmd := exec.Command("mshta.exe", tmpFile)
	cmd.SysProcAttr = &syscall.SysProcAttr{
		CreationFlags: 0x08000000,
	}
	if err := cmd.Start(); err != nil {
		_ = os.Remove(tmpFile)
		return err
	}

	// Clean up temporary HTA file after timeout in background
	go func() {
		time.Sleep(12 * time.Second)
		_ = os.Remove(tmpFile)
	}()

	return nil
}

func escapeHTML(s string) string {
	s = strings.ReplaceAll(s, "&", "&amp;")
	s = strings.ReplaceAll(s, "<", "&lt;")
	s = strings.ReplaceAll(s, ">", "&gt;")
	s = strings.ReplaceAll(s, `"`, "&quot;")
	s = strings.ReplaceAll(s, `'`, "&#39;")
	return s
}
