<#
.SYNOPSIS
    Unregisters DeskPilot from Windows startup.

.DESCRIPTION
    Safely removes the DeskPilot Windows Scheduled Task and/or Startup folder shortcut.

.PARAMETER TaskName
    Name of the scheduled task or startup shortcut to remove. Defaults to 'DeskPilotStartup'.

.PARAMETER Method
    Unregistration target: 'ScheduledTask', 'StartupFolder', or 'All'. Defaults to 'All'.

.EXAMPLE
    .\scripts\unregister_startup.ps1
    Removes DeskPilot scheduled tasks and startup shortcuts.
#>

[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter()]
    [string]$TaskName = "DeskPilotStartup",

    [Parameter()]
    [ValidateSet("ScheduledTask", "StartupFolder", "All")]
    [string]$Method = "All"
)

$ErrorActionPreference = "SilentlyContinue"

# 1. Unregister Scheduled Task
if ($Method -eq "ScheduledTask" -or $Method -eq "All") {
    Write-Host "Checking for scheduled task '$TaskName'..." -ForegroundColor Cyan

    if ($PSCmdlet.ShouldProcess($TaskName, "Unregister Scheduled Task")) {
        $taskRemoved = $false

        try {
            $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            if ($task) {
                Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
                Write-Host "[OK] Scheduled task '$TaskName' removed successfully." -ForegroundColor Green
                $taskRemoved = $true
            }
        } catch {
            # Fallback to schtasks.exe if PowerShell cmdlet encountered issues
            try {
                $schArgs = @("/Delete", "/TN", $TaskName, "/F")
                $proc = Start-Process -FilePath "schtasks.exe" -ArgumentList $schArgs -Wait -PassThru -NoNewWindow
                if ($proc.ExitCode -eq 0) {
                    Write-Host "[OK] Scheduled task '$TaskName' removed via schtasks.exe." -ForegroundColor Green
                    $taskRemoved = $true
                }
            } catch {
                # Ignored
            }
        }

        if (-not $taskRemoved) {
            Write-Host "[INFO] No scheduled task named '$TaskName' found." -ForegroundColor Yellow
        }
    }
}

# 2. Remove Startup Folder Shortcut
if ($Method -eq "StartupFolder" -or $Method -eq "All") {
    $startupDir = [System.IO.Path]::Combine($env:APPDATA, "Microsoft\Windows\Start Menu\Programs\Startup")
    $shortcutPath = Join-Path $startupDir "$TaskName.lnk"

    Write-Host "Checking for startup shortcut at '$shortcutPath'..." -ForegroundColor Cyan

    if (Test-Path $shortcutPath) {
        if ($PSCmdlet.ShouldProcess($shortcutPath, "Delete Startup Shortcut")) {
            try {
                Remove-Item -Path $shortcutPath -Force -ErrorAction Stop
                Write-Host "[OK] Startup shortcut removed: $shortcutPath" -ForegroundColor Green
            } catch {
                Write-Error "Failed to delete startup shortcut: $_"
            }
        }
    } else {
        Write-Host "[INFO] No startup shortcut found at: $shortcutPath" -ForegroundColor Yellow
    }
}

# 3. Remove URL Protocol Handler
if ($Method -eq "All") {
    Write-Host "Checking for 'deskpilot://' protocol handler..." -ForegroundColor Cyan
    $protocolRoot = "HKCU:\Software\Classes\deskpilot"
    if (Test-Path $protocolRoot) {
        if ($PSCmdlet.ShouldProcess($protocolRoot, "Delete URL Protocol Handler")) {
            try {
                Remove-Item -Path $protocolRoot -Recurse -Force -ErrorAction Stop
                Write-Host "[OK] 'deskpilot://' protocol handler removed." -ForegroundColor Green
            } catch {
                Write-Error "Failed to delete protocol handler: $_"
            }
        }
    }
}

Write-Host "`nDeskPilot unregistration complete." -ForegroundColor Green

