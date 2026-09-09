<#
.SYNOPSIS
    Registers DeskPilot to run automatically at user logon on Windows.

.DESCRIPTION
    This script configures Windows Task Scheduler (or the user's Startup folder)
    to launch DeskPilot via 'uv run deskpilot' (optionally with '--startup') whenever
    the current user logs into Windows.

.PARAMETER TaskName
    Name of the scheduled task or startup shortcut. Defaults to 'DeskPilotStartup'.

.PARAMETER Method
    Registration method: 'ScheduledTask' (recommended, uses Task Scheduler),
    'StartupFolder' (places a shortcut in %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup),
    or 'All' (both). Defaults to 'ScheduledTask'.

.PARAMETER StartupFlag
    If specified, adds the '--startup' flag to the command, running non-interactive boot tasks.

.PARAMETER Force
    Overwrites any existing scheduled task or startup shortcut without prompting.

.PARAMETER WorkingDirectory
    Working directory for execution. Defaults to the DeskPilot repository root.

.EXAMPLE
    .\scripts\register_startup.ps1
    Registers DeskPilot in Task Scheduler with interactive dashboard at logon.

.EXAMPLE
    .\scripts\register_startup.ps1 -StartupFlag
    Registers DeskPilot in Task Scheduler to run automated boot checks on logon.

.EXAMPLE
    .\scripts\register_startup.ps1 -Method StartupFolder
    Creates a shortcut in the user's Startup folder.
#>

[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter()]
    [string]$TaskName = "DeskPilotStartup",

    [Parameter()]
    [ValidateSet("ScheduledTask", "StartupFolder", "All")]
    [string]$Method = "ScheduledTask",

    [Parameter()]
    [switch]$StartupFlag,

    [Parameter()]
    [switch]$Force,

    [Parameter()]
    [string]$WorkingDirectory
)

$ErrorActionPreference = "Stop"

# 1. Determine Repository Root Directory
if (-not $WorkingDirectory) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $WorkingDirectory = (Resolve-Path (Join-Path $scriptDir "..")).Path
}

if (-not (Test-Path $WorkingDirectory)) {
    Write-Error "WorkingDirectory does not exist: $WorkingDirectory"
    exit 1
}

Write-Host "DeskPilot Project Root: $WorkingDirectory" -ForegroundColor Cyan

# 2. Locate 'uv' executable
$uvCommand = Get-Command "uv" -ErrorAction SilentlyContinue
if ($uvCommand) {
    $uvPath = $uvCommand.Source
} else {
    $userUv = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
    $cargoUv = Join-Path $env:USERPROFILE ".cargo\bin\uv.exe"
    if (Test-Path $userUv) {
        $uvPath = $userUv
    } elseif (Test-Path $cargoUv) {
        $uvPath = $cargoUv
    } else {
        $uvPath = "uv"
        Write-Warning "'uv' executable not found in PATH or standard user directories. Using fallback 'uv'."
    }
}

Write-Host "Using uv executable: $uvPath" -ForegroundColor Cyan

# 3. Construct arguments
$cliArgs = "run deskpilot"
if ($StartupFlag) {
    $cliArgs += " --startup"
}

Write-Host "Command to register: $uvPath $cliArgs" -ForegroundColor Cyan

# 4. Register via Scheduled Task
if ($Method -eq "ScheduledTask" -or $Method -eq "All") {
    Write-Host "`nRegistering Windows Scheduled Task '$TaskName'..." -ForegroundColor Yellow

    if ($PSCmdlet.ShouldProcess($TaskName, "Register Scheduled Task")) {
        try {
            $taskExists = $false
            try {
                $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
                if ($existing) { $taskExists = $true }
            } catch {
                $taskExists = $false
            }

            if ($taskExists -and -not $Force) {
                Write-Warning "Scheduled task '$TaskName' already exists. Use -Force to overwrite."
            } else {
                $action = New-ScheduledTaskAction -Execute $uvPath -Argument $cliArgs -WorkingDirectory $WorkingDirectory
                $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
                $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
                $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)

                Register-ScheduledTask -TaskName $TaskName `
                    -Action $action `
                    -Trigger $trigger `
                    -Principal $principal `
                    -Settings $settings `
                    -Description "DeskPilot Morning Command Center & Boot Orchestrator" `
                    -Force | Out-Null

                Write-Host "[OK] Scheduled task '$TaskName' registered successfully." -ForegroundColor Green
            }
        } catch {
            Write-Warning "PowerShell Register-ScheduledTask failed: $_. Attempting schtasks.exe fallback..."
            try {
                $schArgs = @(
                    "/Create",
                    "/TN", $TaskName,
                    "/TR", "`"$uvPath`" $cliArgs",
                    "/SC", "ONLOGON",
                    "/F"
                )
                $proc = Start-Process -FilePath "schtasks.exe" -ArgumentList $schArgs -Wait -PassThru -NoNewWindow
                if ($proc.ExitCode -eq 0) {
                    Write-Host "[OK] Scheduled task '$TaskName' created via schtasks.exe." -ForegroundColor Green
                } else {
                    Write-Error "Failed to register scheduled task via schtasks (Exit code: $($proc.ExitCode))."
                }
            } catch {
                Write-Error "Failed to register scheduled task: $_"
            }
        }
    }
}

# 5. Register via Startup Folder Shortcut
if ($Method -eq "StartupFolder" -or $Method -eq "All") {
    Write-Host "`nCreating Startup folder shortcut '$TaskName.lnk'..." -ForegroundColor Yellow

    $startupDir = [System.IO.Path]::Combine($env:APPDATA, "Microsoft\Windows\Start Menu\Programs\Startup")
    if (-not (Test-Path $startupDir)) {
        New-Item -Path $startupDir -ItemType Directory -Force | Out-Null
    }

    $shortcutPath = Join-Path $startupDir "$TaskName.lnk"

    if ($PSCmdlet.ShouldProcess($shortcutPath, "Create Startup Shortcut")) {
        try {
            if ((Test-Path $shortcutPath) -and -not $Force) {
                Write-Warning "Startup shortcut '$shortcutPath' already exists. Use -Force to overwrite."
            } else {
                $wscript = New-Object -ComObject WScript.Shell
                $shortcut = $wscript.CreateShortcut($shortcutPath)
                $shortcut.TargetPath = $uvPath
                $shortcut.Arguments = $cliArgs
                $shortcut.WorkingDirectory = $WorkingDirectory
                $shortcut.Description = "DeskPilot Morning Command Center"
                $shortcut.Save()

                Write-Host "[OK] Startup shortcut created successfully at: $shortcutPath" -ForegroundColor Green
            }
        } catch {
            Write-Error "Failed to create startup shortcut: $_"
        }
    }
}

Write-Host "`nDeskPilot startup registration complete!" -ForegroundColor Green
