"""Unit tests for Windows startup integration PowerShell scripts."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
REGISTER_SCRIPT = SCRIPTS_DIR / "register_startup.ps1"
UNREGISTER_SCRIPT = SCRIPTS_DIR / "unregister_startup.ps1"


def test_startup_scripts_exist():
    """Verify that both startup registration and unregistration scripts exist."""
    assert REGISTER_SCRIPT.exists(), f"Missing script: {REGISTER_SCRIPT}"
    assert UNREGISTER_SCRIPT.exists(), f"Missing script: {UNREGISTER_SCRIPT}"


def test_startup_scripts_no_utf8_bom():
    """Verify that PowerShell scripts do not contain UTF-8 BOM headers."""
    for script_path in [REGISTER_SCRIPT, UNREGISTER_SCRIPT]:
        content = script_path.read_bytes()
        assert not content.startswith(b"\xef\xbb\xbf"), (
            f"Script {script_path} contains a UTF-8 BOM header."
        )


def test_register_startup_script_contains_key_elements():
    """Verify register_startup.ps1 contains necessary commands, parameters, and error handling."""
    content = REGISTER_SCRIPT.read_text(encoding="utf-8")
    assert "param(" in content or "param (" in content
    assert "TaskName" in content
    assert "uv" in content
    assert "deskpilot" in content
    assert "StartupFlag" in content or "startup" in content.lower()
    assert "ScheduledTask" in content or "schtasks" in content
    assert "--directory" in content


def test_unregister_startup_script_contains_key_elements():
    """Verify unregister_startup.ps1 contains necessary cleanup logic."""
    content = UNREGISTER_SCRIPT.read_text(encoding="utf-8")
    assert "param(" in content or "param (" in content
    assert "TaskName" in content
    assert "Unregister-ScheduledTask" in content or "schtasks" in content or "Delete" in content


@pytest.mark.skipif(
    shutil.which("powershell") is None and shutil.which("pwsh") is None,
    reason="PowerShell not available on this system",
)
def test_powershell_scripts_syntax_validity():
    """Validate PowerShell AST syntax validity using System.Management.Automation.Language.Parser."""
    ps_cmd = shutil.which("pwsh") or shutil.which("powershell") or "powershell"

    for script_path in [REGISTER_SCRIPT, UNREGISTER_SCRIPT]:
        # Escape path for powershell
        resolved = str(script_path.resolve()).replace("'", "''")
        ps_code = f"""
        $errors = $null
        $tokens = $null
        [System.Management.Automation.Language.Parser]::ParseFile('{resolved}', [ref]$tokens, [ref]$errors) | Out-Null
        if ($errors -and $errors.Count -gt 0) {{
            $errors | ForEach-Object {{ Write-Error $_.Message }}
            exit 1
        }}
        exit 0
        """
        proc = subprocess.run(
            [ps_cmd, "-NoProfile", "-NonInteractive", "-Command", ps_code],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, (
            f"PowerShell syntax validation failed for {script_path.name}:\n{proc.stderr}"
        )
