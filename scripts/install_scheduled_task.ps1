<#
.SYNOPSIS
  Install a Windows scheduled task to run the DataScrubb pipeline on a cadence.

.DESCRIPTION
  Creates a Windows Task Scheduler entry named "DataScrubb-Pipeline" that runs
  run_pipeline.py via the project's venv on the schedule you specify (default:
  daily at 6:00 AM). Re-running this script overwrites the existing task.

.PARAMETER Time
  Time of day to run, "HH:mm" 24h format. Default: "06:00".

.PARAMETER DaysOfWeek
  Days to run on. Default: every day. Pass as comma-separated, e.g. "Monday,Wednesday,Friday".

.PARAMETER TaskName
  Name of the scheduled task. Default: "DataScrubb-Pipeline".

.PARAMETER ProjectRoot
  Absolute path to the project root. Defaults to the parent of this script's directory.

.EXAMPLE
  .\install_scheduled_task.ps1
  .\install_scheduled_task.ps1 -Time 03:30
  .\install_scheduled_task.ps1 -Time 07:00 -DaysOfWeek "Monday,Wednesday,Friday"

.NOTES
  Run this script from PowerShell as Administrator.
  Uninstall with: Unregister-ScheduledTask -TaskName "DataScrubb-Pipeline" -Confirm:$false
#>

param(
    [string]$Time = "06:00",
    [string]$DaysOfWeek = "",
    [string]$TaskName = "DataScrubb-Pipeline",
    [string]$ProjectRoot = ""
)

if (-not $ProjectRoot) {
    $ProjectRoot = Split-Path -Path $PSScriptRoot -Parent
}
$ProjectRoot = (Resolve-Path $ProjectRoot).Path

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Script = Join-Path $ProjectRoot "run_pipeline.py"
$LogDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$LogFile = Join-Path $LogDir "scheduled_run.log"

if (-not (Test-Path $Python)) {
    Write-Error "venv Python not found at $Python. Run .venv setup first."
    exit 1
}
if (-not (Test-Path $Script)) {
    Write-Error "Pipeline launcher not found at $Script."
    exit 1
}

Write-Host "Installing scheduled task '$TaskName'..." -ForegroundColor Cyan
Write-Host "  Python:       $Python"
Write-Host "  Script:       $Script"
Write-Host "  Log:          $LogFile"
Write-Host "  Time:         $Time"
Write-Host "  Days:         $(if ($DaysOfWeek) {$DaysOfWeek} else {'every day'})"

$Action = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "`"$Script`"" `
    -WorkingDirectory $ProjectRoot

if ($DaysOfWeek) {
    $days = $DaysOfWeek.Split(',') | ForEach-Object { $_.Trim() }
    $Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $Time
} else {
    $Trigger = New-ScheduledTaskTrigger -Daily -At $Time
}

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable:$false

$Principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

# Unregister existing task with same name (if any)
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Removing existing task..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Runs the DataScrubb pipeline against data files in $ProjectRoot." | Out-Null

Write-Host ""
Write-Host "Done. Task '$TaskName' installed." -ForegroundColor Green
Write-Host ""
Write-Host "Next run: " -NoNewline
(Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo).NextRunTime

Write-Host ""
Write-Host "Manage with:"
Write-Host "  Get-ScheduledTask -TaskName $TaskName"
Write-Host "  Start-ScheduledTask -TaskName $TaskName            # run now"
Write-Host "  Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
