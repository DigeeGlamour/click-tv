param(
    [ValidateSet("channels", "today", "upcoming", "movies", "all")]
    [string]$Mode = "all",
    [switch]$NoPush
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonCommand = Get-Command python -ErrorAction SilentlyContinue
$AutoPush = -not $NoPush

if (-not $PythonCommand) {
    throw "Python was not found. Install Python 3.11+ from python.org and enable Add Python to PATH."
}

Set-Location -LiteralPath $ProjectRoot

function Invoke-Git {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & git @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Git command failed: git $($Arguments -join ' ')"
    }
}

function Assert-CleanRepository {
    $Remaining = (& git status --porcelain --untracked-files=normal) -join "`n"
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to read Git repository status."
    }
    if ($Remaining.Trim()) {
        Write-Host $Remaining
        throw "Repository already has uncommitted files. Commit or discard them before an automatic scan/push."
    }
}

function Test-GeneratedPages {
    $TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("clicktv-pages-" + [guid]::NewGuid().ToString("N"))
    try {
        New-Item -ItemType Directory -Path $TempRoot | Out-Null
        Copy-Item -Path (Join-Path $ProjectRoot "site\*") -Destination $TempRoot -Recurse -Force
        $TempData = Join-Path $TempRoot "data"
        New-Item -ItemType Directory -Path $TempData | Out-Null
        Copy-Item -Path (Join-Path $ProjectRoot "data\*") -Destination $TempData -Recurse -Force
        & $PythonCommand.Source (Join-Path $ProjectRoot "scripts\validate-pages.py") $TempRoot
        if ($LASTEXITCODE -ne 0) {
            throw "Generated Cloudflare Pages data validation failed. GitHub push was stopped."
        }
    }
    finally {
        $ResolvedTempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
        $ResolvedTarget = [System.IO.Path]::GetFullPath($TempRoot)
        if ($ResolvedTarget.StartsWith($ResolvedTempBase, [System.StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $ResolvedTarget)) {
            Remove-Item -LiteralPath $ResolvedTarget -Recurse -Force
        }
    }
}

if ($AutoPush) {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "Git was not found. Install Git for Windows and sign in with GitHub Desktop first."
    }

    $GitRoot = (& git rev-parse --show-toplevel 2>$null) -join ""
    if ($LASTEXITCODE -ne 0 -or -not $GitRoot.Trim()) {
        throw "This folder is not a Git clone. Clone DigeeGlamour/click-tv with GitHub Desktop, then run this file from that cloned folder. A downloaded ZIP cannot auto-push."
    }

    $ResolvedProject = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\', '/')
    $ResolvedGitRoot = [System.IO.Path]::GetFullPath($GitRoot.Trim()).TrimEnd('\', '/')
    if (-not $ResolvedProject.Equals($ResolvedGitRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "The launcher must be run from the root of the click-tv Git clone."
    }

    Write-Host "[GIT] Checking clean main branch and downloading latest changes..." -ForegroundColor Cyan
    Assert-CleanRepository
    Invoke-Git -Arguments @("fetch", "origin", "main")
    Invoke-Git -Arguments @("checkout", "main")
    Invoke-Git -Arguments @("pull", "--rebase", "origin", "main")
    Assert-CleanRepository
}

Write-Host "Click TV local scanner" -ForegroundColor Cyan
Write-Host "Project   : $ProjectRoot"
Write-Host "Mode      : $Mode"
Write-Host "Auto Push : $AutoPush"
Write-Host "Network   : This PC's current Internet/IP will be used (useful for BD-IP sources)."

& $PythonCommand.Source -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw "Python dependency installation failed."
}

& $PythonCommand.Source -u scan.py $Mode
if ($LASTEXITCODE -ne 0) {
    throw "Click TV scan failed. GitHub push was not attempted. Check working/scan-progress.json and reports/source-errors.json."
}

Write-Host "[VALIDATE] Testing generated website data before push..." -ForegroundColor Cyan
Test-GeneratedPages

if (-not $AutoPush) {
    Write-Host "Scan and validation completed. NoPush was selected, so GitHub was not changed." -ForegroundColor Green
    exit 0
}

Write-Host "[GIT] Committing generated data..." -ForegroundColor Cyan
Invoke-Git -Arguments @("add", "-A", "--", "data", "reports", "state")

& git diff --cached --quiet
$HasChanges = $LASTEXITCODE -ne 0
if ($HasChanges) {
    $Timestamp = [DateTime]::UtcNow.ToString("yyyy-MM-dd HH:mm:ss 'UTC'")
    Invoke-Git -Arguments @("commit", "-m", "Local auto update: $Mode [$Timestamp]")
}
else {
    Write-Host "No generated output changed; current main will still be synchronized."
}

& git restore --worktree -- working 2>$null
if (Test-Path -LiteralPath (Join-Path $ProjectRoot "working\pipeline-checkpoint.json")) {
    Remove-Item -LiteralPath (Join-Path $ProjectRoot "working\pipeline-checkpoint.json") -Force
}
if (Test-Path -LiteralPath (Join-Path $ProjectRoot "working\checkpoints")) {
    Remove-Item -LiteralPath (Join-Path $ProjectRoot "working\checkpoints") -Recurse -Force
}
Assert-CleanRepository

for ($Attempt = 1; $Attempt -le 3; $Attempt++) {
    Invoke-Git -Arguments @("fetch", "origin", "main")
    Invoke-Git -Arguments @("rebase", "origin/main")
    & git push origin HEAD:main
    if ($LASTEXITCODE -eq 0) {
        $Commit = (& git rev-parse --short HEAD) -join ""
        Write-Host "SCAN + VALIDATION + GITHUB AUTO-PUSH COMPLETED: $Commit" -ForegroundColor Green
        exit 0
    }
    if ($Attempt -lt 3) {
        Write-Host "Push attempt $Attempt failed; retrying..." -ForegroundColor Yellow
        Start-Sleep -Seconds 5
    }
}

throw "GitHub push failed after 3 attempts. The scan commit remains safely in this local clone; run the launcher again after fixing login/network."
