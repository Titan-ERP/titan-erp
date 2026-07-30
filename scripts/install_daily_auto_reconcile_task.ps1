param(
    [string]$At = "11:45PM",
    [string]$TaskName = "Odoo Daily Auto Reconcile Agent"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Runner = Join-Path $Root "scripts\run_daily_auto_reconcile.ps1"

if (-not (Test-Path $Runner)) {
    throw "Runner not found: $Runner"
}

$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $Runner)
$Trigger = New-ScheduledTaskTrigger -Daily -At $At
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Runs the guarded Odoo daily auto reconciliation agent for the current day's Laurel bank lines." `
    -Force | Out-Null

Write-Host ("Installed scheduled task '{0}' at {1}." -f $TaskName, $At)
Write-Host ("Runner: {0}" -f $Runner)
