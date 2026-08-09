param(
    [ValidateSet("channels", "today", "upcoming", "movies", "all")]
    [string]$Mode = "all"
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonCommand = Get-Command python -ErrorAction SilentlyContinue

if (-not $PythonCommand) {
    throw "Python was not found. Install Python 3.11+ from python.org and enable Add Python to PATH."
}

Set-Location -LiteralPath $ProjectRoot

Write-Host "Click TV local scanner" -ForegroundColor Cyan
Write-Host "Project : $ProjectRoot"
Write-Host "Mode    : $Mode"
Write-Host "Network : This PC's current Internet/IP will be used (useful for BD-IP sources)."

& $PythonCommand.Source -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw "Python dependency installation failed."
}

& $PythonCommand.Source -u scan.py $Mode
if ($LASTEXITCODE -ne 0) {
    throw "Click TV scan failed. Check working/scan-progress.json and reports/source-errors.json."
}

Write-Host "Scan completed. Generated files are in data/, reports/, and state/." -ForegroundColor Green
Write-Host "This script does not push to GitHub. Review the output, then commit/push with GitHub Desktop."
