param(
    [string]$DateFrom = "2026-01-01",
    [string]$DateTo = (Get-Date -Format "yyyy-MM-dd"),
    [string]$OutputDate = (Get-Date -Format "yyyy-MM-dd")
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$LogDir = Join-Path $Root ("odoo_imports\accounting\bank_matching_analysis\{0}" -f $OutputDate)
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogPath = Join-Path $LogDir ("bank_matching_analysis_{0}.log" -f $Stamp)
$Py = (Get-Command py -ErrorAction Stop).Source

Push-Location $Root
try {
    & $Py -3.13 "scripts\odoo_bank_matching_analysis_agent.py" `
        --date-from $DateFrom `
        --date-to $DateTo `
        --output-date $OutputDate *> $LogPath
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
