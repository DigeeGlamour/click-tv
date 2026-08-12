$ErrorActionPreference = "Stop"

$RepositoryRoot = Split-Path -Parent $PSScriptRoot

$Launcher = Get-Content -LiteralPath (Join-Path $RepositoryRoot "CLICK_TV_EASY_PAT_SCAN.cmd") -Raw
$Match = [regex]::Match($Launcher, '(?s)\$FilesToSync\s*=\s*@\((.*?)\r?\n\)')
if (-not $Match.Success) { throw "FilesToSync block was not found" }

$Files = [regex]::Matches($Match.Groups[1].Value, '"([^"]+)"') |
    ForEach-Object { $_.Groups[1].Value }

$Missing = @($Files | Where-Object {
    -not (Test-Path -LiteralPath (Join-Path $RepositoryRoot $_))
})

if ($Missing.Count) {
    throw "Package incomplete: $($Missing -join ', ')"
}

if ($Files -contains "scan.py") {
    throw "Partial update launcher must use scan.py from the fresh GitHub clone"
}

Write-Host "LOCAL UPDATE PACKAGE PREFLIGHT PASS ($($Files.Count) synced files)"
