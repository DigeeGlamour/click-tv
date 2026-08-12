@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Click TV - Easy PAT Scan and GitHub Push
set "CLICKTV_SELF=%~f0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$raw=Get-Content -LiteralPath $env:CLICKTV_SELF -Raw; $marker='# CLICKTV_'+'POWERSHELL_BEGIN'; $at=$raw.IndexOf($marker); if($at -lt 0){throw 'Internal launcher code is missing'}; $code=$raw.Substring($at+$marker.Length); try { & ([scriptblock]::Create($code)) } catch { $safe=[string]$_; foreach($name in @('PRIVATE_MOVIE_SOURCE_TOKEN','CLICKTV_TEST_TOKEN','GIT_CONFIG_VALUE_0')){$secret=[Environment]::GetEnvironmentVariable($name); if($secret -and $secret.Length -ge 8){$safe=$safe.Replace($secret,'[REDACTED]')}}; [Console]::Error.WriteLine($safe); exit 1 }"
set "CLICKTV_EXIT=%ERRORLEVEL%"

echo.
if "%CLICKTV_EXIT%"=="0" (
  echo ============================================================
  echo SCAN AND GITHUB PUSH COMPLETED SUCCESSFULLY
  echo ============================================================
) else (
  echo ============================================================
  echo PROCESS FAILED - exit code %CLICKTV_EXIT%
  echo Keep this window open and send a screenshot of the red error.
  echo ============================================================
)
echo.
if "%CLICKTV_NO_PAUSE%"=="1" exit /b %CLICKTV_EXIT%
pause
exit /b %CLICKTV_EXIT%

# CLICKTV_POWERSHELL_BEGIN
$ErrorActionPreference = "Stop"
$SelfPath = [IO.Path]::GetFullPath($env:CLICKTV_SELF)
$PackageRoot = Split-Path -Parent $SelfPath
$ClonePath = Join-Path (Join-Path $env:USERPROFILE "Downloads") "ClickTV-Auto"
$RepositoryUrl = "https://github.com/DigeeGlamour/click-tv.git"

function Invoke-Git {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [string]$WorkingDirectory = "",
        [switch]$AllowFailure
    )
    if ($WorkingDirectory) {
        & git -C $WorkingDirectory @Arguments
    }
    else {
        & git @Arguments
    }
    $Code = $LASTEXITCODE
    if ($Code -ne 0 -and -not $AllowFailure) {
        throw "Git command failed: git $($Arguments -join ' ')"
    }
    return $Code
}

function Assert-SupportedDirtyState {
    param([string]$RepositoryPath)
    $Allowed = @(
        "scan.py",
        "scanner/security.py",
        "scanner/playback_profiles.py",
        "scanner/output.py",
        "scanner/telegram_notify.py",
        "scanner/normalizer.py",
        ".github/workflows/scan.yml",
        ".gitignore",
        "config/channel-categories.json",
        "config/channel-aliases.json",
        "config/sources.json",
        "config/settings.json",
        "config/event-fixtures.json",
        "scanner/events.py",
        "scanner/merger.py",
        "scanner/schedule_resolver.py",
        "site/assets/js/app.js",
        "site/sw.js",
        "scripts/browser-event-card-check.mjs",
        "tests/test_schedule_resolver.py",
        "scripts/run-local-scan.ps1",
        "tests/test_zero_candidate_preservation.py",
        "tests/test_content_router.py",
        "tests/test_operational_safety.py",
        "CLICK_TV_EASY_PAT_SCAN.cmd",
        "CLOUDFLARE_GITHUB_SETUP_BN.md",
        "ClickTV_Colab_FINAL_EASY_5_MODE.ipynb",
        "working/scan-progress.json"
    )
    $Unexpected = @()
    foreach ($Line in (& git -C $RepositoryPath status --porcelain)) {
        if (-not $Line) { continue }
        # Git status always uses forward slashes. Normalize Windows separators
        # the same way before comparing with the allow-list.
        $Path = $Line.Substring(3).Trim().Trim('"').Replace('\', '/')
        if ($Path -notin $Allowed) { $Unexpected += $Line }
    }
    if ($Unexpected.Count) {
        Write-Host ($Unexpected -join "`n")
        throw "ClickTV-Auto contains unrelated unfinished files. They were not overwritten."
    }
}

function Test-GeneratedPages {
    param([string]$RepositoryPath, [string]$PythonPath)
    $TempRoot = Join-Path ([IO.Path]::GetTempPath()) ("clicktv-validate-" + [guid]::NewGuid().ToString("N"))
    try {
        New-Item -ItemType Directory -Path $TempRoot | Out-Null
        Copy-Item -Path (Join-Path $RepositoryPath "site\*") -Destination $TempRoot -Recurse -Force
        $TempData = Join-Path $TempRoot "data"
        New-Item -ItemType Directory -Path $TempData | Out-Null
        Copy-Item -Path (Join-Path $RepositoryPath "data\*") -Destination $TempData -Recurse -Force
        & $PythonPath (Join-Path $RepositoryPath "scripts\validate-pages.py") $TempRoot
        if ($LASTEXITCODE -ne 0) { throw "Pages validation failed; GitHub push was stopped." }
    }
    finally {
        $TempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
        $Target = [IO.Path]::GetFullPath($TempRoot)
        if ($Target.StartsWith($TempBase, [StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $Target)) {
            Remove-Item -LiteralPath $Target -Recurse -Force
        }
    }
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "Git for Windows is not installed." }
$PythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $PythonCommand) { throw "Python 3.11+ is not installed or not in PATH." }

Clear-Host
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "       CLICK TV - EASY PAT SCANNER + GITHUB AUTO PUSH" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "One file only. GitHub Desktop is not required."
Write-Host "PAT is used only inside this window and is not saved."
Write-Host "Paste remains hidden."
Write-Host ""

$SecureToken = if ($env:CLICKTV_TEST_TOKEN) {
    ConvertTo-SecureString $env:CLICKTV_TEST_TOKEN -AsPlainText -Force
} else {
    Read-Host "Paste GitHub PAT" -AsSecureString
}
$Pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureToken)
try {
    $PlainToken = ([Runtime.InteropServices.Marshal]::PtrToStringBSTR($Pointer)).Trim().Trim('"').Trim("'").Trim()
    if (-not $PlainToken -or $PlainToken.Length -lt 20) {
        throw "PAT is empty or incomplete. Copy the complete token."
    }
    $BasicValue = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("x-access-token:$PlainToken"))
    $env:GIT_CONFIG_COUNT = "1"
    $env:GIT_CONFIG_KEY_0 = "http.https://github.com/.extraHeader"
    $env:GIT_CONFIG_VALUE_0 = "AUTHORIZATION: basic $BasicValue"
    $env:PRIVATE_MOVIE_SOURCE_TOKEN = $PlainToken
}
finally {
    if ($Pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Pointer) }
    $PlainToken = $null
    $BasicValue = $null
}

Write-Host ""
Write-Host "Select scan mode:" -ForegroundColor Cyan
Write-Host "  1. Full scan - ALL [Recommended]"
Write-Host "  2. TV Channels only"
Write-Host "  3. Movies only"
Write-Host "  4. Today Match only"
Write-Host "  5. Upcoming only"
Write-Host "  Q. Close without scanning"
$Choice = if ($env:CLICKTV_TEST_CHOICE) {
    $env:CLICKTV_TEST_CHOICE.Trim()
} else {
    (Read-Host "Type 1, 2, 3, 4, 5 or Q").Trim()
}
$ModeMap = @{ "1"="all"; "2"="channels"; "3"="movies"; "4"="today"; "5"="upcoming" }
if ($Choice.Equals("Q", [StringComparison]::OrdinalIgnoreCase)) { exit 0 }
$Mode = $ModeMap[$Choice]
if (-not $Mode) { throw "Invalid scan option. Run the same file again and choose 1-5." }

Write-Host ""
Write-Host "[1/6] Preparing Git repository..." -ForegroundColor Cyan
if (Test-Path -LiteralPath $ClonePath) {
    if (-not (Test-Path -LiteralPath (Join-Path $ClonePath ".git"))) {
        throw "Downloads\ClickTV-Auto exists but is not a Git clone. Rename that folder and run again."
    }
    Assert-SupportedDirtyState -RepositoryPath $ClonePath
}
else {
    Invoke-Git -Arguments @("clone", "--branch", "main", "--single-branch", $RepositoryUrl, $ClonePath) | Out-Null
}

# If a previous scan completed and committed but its final push was interrupted,
# push that result once and do not waste hours repeating the same full scan.
Invoke-Git -WorkingDirectory $ClonePath -Arguments @("fetch", "origin", "main") | Out-Null
$PendingScanSubjects = @(& git -C $ClonePath log --format=%s origin/main..HEAD)
$RecoveredPendingScan = @(
    $PendingScanSubjects | Where-Object { $_ -like "Local auto update:*" }
).Count -gt 0

$OldProgress = Join-Path $ClonePath "working\scan-progress.json"
if (Test-Path -LiteralPath $OldProgress) {
    Remove-Item -LiteralPath $OldProgress -Force
}

$FilesToSync = @(
    "ClickTV_Colab_FINAL_EASY_5_MODE.ipynb",
    "config\sources.json",
    "config\settings.json",
    "config\event-fixtures.json",
    "scanner\events.py",
    "scanner\merger.py",
    "scanner\schedule_resolver.py",
    "site\assets\js\app.js",
    "site\sw.js",
    "scripts\browser-event-card-check.mjs",
    "scripts\test-update-package.ps1",
    "tests\test_schedule_resolver.py",
    "tests\test_operational_safety.py"
)
foreach ($RelativePath in $FilesToSync) {
    $Source = Join-Path $PackageRoot $RelativePath
    if (-not (Test-Path -LiteralPath $Source)) {
        throw "Update package file is missing: $RelativePath. Extract the complete Ruman-17 package and run this CMD from inside that folder."
    }
    $Destination = Join-Path $ClonePath $RelativePath
    New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Destination -Force
}
Copy-Item -LiteralPath $SelfPath -Destination (Join-Path $ClonePath "CLICK_TV_EASY_PAT_SCAN.cmd") -Force
$FilesToSync += "CLICK_TV_EASY_PAT_SCAN.cmd"

Invoke-Git -WorkingDirectory $ClonePath -Arguments @("config", "user.name", "Click TV Local Scanner") | Out-Null
Invoke-Git -WorkingDirectory $ClonePath -Arguments @("config", "user.email", "clicktv-local@users.noreply.github.com") | Out-Null
$AddCodeArguments = @("add", "--") + $FilesToSync
Invoke-Git -WorkingDirectory $ClonePath -Arguments $AddCodeArguments | Out-Null
& git -C $ClonePath diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    Invoke-Git -WorkingDirectory $ClonePath -Arguments @("commit", "-m", "Fix scanner and add easy PAT scan launcher") | Out-Null
}
Invoke-Git -WorkingDirectory $ClonePath -Arguments @("fetch", "origin", "main") | Out-Null
Invoke-Git -WorkingDirectory $ClonePath -Arguments @("rebase", "origin/main") | Out-Null
Invoke-Git -WorkingDirectory $ClonePath -Arguments @("push", "origin", "HEAD:main") | Out-Null

if ($RecoveredPendingScan) {
    $RecoveredCommit = (& git -C $ClonePath rev-parse --short HEAD) -join ""
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "PREVIOUS COMPLETED SCAN WAS RECOVERED AND PUSHED" -ForegroundColor Green
    Write-Host "No duplicate scan was started. Commit: $RecoveredCommit"
    Write-Host "============================================================" -ForegroundColor Green
    exit 0
}

Write-Host "[2/6] Installing/checking scanner requirements..." -ForegroundColor Cyan
$VenvPath = Join-Path $ClonePath ".venv"
$VenvPython = Join-Path $VenvPath "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    Write-Host "Creating isolated Click TV Python environment..." -ForegroundColor Cyan
    & $PythonCommand.Source -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) { throw "Could not create the Click TV virtual environment." }
}
& $VenvPython -m pip install --disable-pip-version-check -r (Join-Path $ClonePath "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed." }

Write-Host "[3/6] Running scan mode: $Mode" -ForegroundColor Cyan
$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"
& $VenvPython -u (Join-Path $ClonePath "scan.py") $Mode
if ($LASTEXITCODE -ne 0) { throw "Scanner failed. No generated data was pushed." }

Write-Host "[4/6] Validating generated Pages data..." -ForegroundColor Cyan
Test-GeneratedPages -RepositoryPath $ClonePath -PythonPath $VenvPython

Write-Host "[5/6] Committing generated data..." -ForegroundColor Cyan
Invoke-Git -WorkingDirectory $ClonePath -Arguments @("add", "-A", "--", "data", "reports", "state") | Out-Null
& git -C $ClonePath diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    $Timestamp = [DateTime]::UtcNow.ToString("yyyy-MM-dd HH:mm:ss 'UTC'")
    Invoke-Git -WorkingDirectory $ClonePath -Arguments @("commit", "-m", "Local auto update: $Mode [$Timestamp]") | Out-Null
}

& git -C $ClonePath restore --worktree -- working 2>$null
$Checkpoint = Join-Path $ClonePath "working\pipeline-checkpoint.json"
if (Test-Path -LiteralPath $Checkpoint) { Remove-Item -LiteralPath $Checkpoint -Force }
$CheckpointDirectory = Join-Path $ClonePath "working\checkpoints"
if (Test-Path -LiteralPath $CheckpointDirectory) { Remove-Item -LiteralPath $CheckpointDirectory -Recurse -Force }
$ProgressFile = Join-Path $ClonePath "working\scan-progress.json"
if (Test-Path -LiteralPath $ProgressFile) { Remove-Item -LiteralPath $ProgressFile -Force }

$Remaining = (& git -C $ClonePath status --porcelain) -join "`n"
if ($Remaining.Trim()) {
    Write-Host $Remaining
    throw "Unexpected files remain; final push was stopped to protect the repository."
}

Write-Host "[6/6] Pushing scan result to GitHub..." -ForegroundColor Cyan
Invoke-Git -WorkingDirectory $ClonePath -Arguments @("fetch", "origin", "main") | Out-Null
Invoke-Git -WorkingDirectory $ClonePath -Arguments @("rebase", "origin/main") | Out-Null
Invoke-Git -WorkingDirectory $ClonePath -Arguments @("push", "origin", "HEAD:main") | Out-Null
$Commit = (& git -C $ClonePath rev-parse --short HEAD) -join ""

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "COMPLETED: $Mode SCAN + GITHUB PUSH" -ForegroundColor Green
Write-Host "Commit: $Commit"
Write-Host "Next time run: $ClonePath\CLICK_TV_EASY_PAT_SCAN.cmd"
Write-Host "============================================================" -ForegroundColor Green
