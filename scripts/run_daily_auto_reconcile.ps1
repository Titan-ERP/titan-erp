param(
    [string]$Date = (Get-Date -Format "yyyy-MM-dd"),
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$LogDir = Join-Path $Root ("odoo_imports\accounting\daily_auto_reconcile\{0}" -f $Date)
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogPath = Join-Path $LogDir ("daily_auto_reconcile_{0}.log" -f $Stamp)
$ArgsList = @("scripts\odoo_daily_auto_reconcile_agent.py", "--date", $Date)
if (-not $DryRun) {
    $ArgsList += "--apply"
}

Push-Location $Root
try {
    & py @ArgsList *> $LogPath
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
