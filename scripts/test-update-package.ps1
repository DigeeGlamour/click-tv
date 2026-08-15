$ErrorActionPreference = "Stop"

$RepositoryRoot = Split-Path -Parent $PSScriptRoot

$Launcher = Get-Content -LiteralPath (Join-Path $RepositoryRoot "CLICK_TV_EASY_PAT_SCAN.cmd") -Raw
foreach ($Forbidden in @(
    '$FilesToSync',
    'Copy-Item -LiteralPath $Source',
    'Fix scanner and add easy PAT scan launcher'
)) {
    if ($Launcher.Contains($Forbidden)) {
        throw "Local scanner still contains forbidden code-sync behavior: $Forbidden"
    }
}

foreach ($Required in @(
    'ClickTV-Data-Scanner',
    'Invoke-RebaseAndPush',
    '@("add", "-A", "--", "data", "reports", "state")',
    '@("reset", "--hard", "origin/main")',
    '@("rebase", "--abort")',
    '"clone", "--depth", "1", "--no-tags"',
    'Test-UsableScannerClone',
    'Move-IncompleteScannerClone',
    'http.lowSpeedTime=30'
)) {
    if (-not $Launcher.Contains($Required)) {
        throw "Local data-only scanner requirement is missing: $Required"
    }
}

Write-Host "LOCAL DATA-ONLY SCANNER PREFLIGHT PASS"
