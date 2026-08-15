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
$ClonePath = if ($env:CLICKTV_CLONE_PATH) {
    [IO.Path]::GetFullPath($env:CLICKTV_CLONE_PATH)
} else {
    Join-Path (Join-Path $env:USERPROFILE "Downloads") "ClickTV-Data-Scanner"
}
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

function Invoke-RebaseAndPush {
    param([string]$RepositoryPath)
    Invoke-Git -WorkingDirectory $RepositoryPath -Arguments @("fetch", "origin", "main") | Out-Null
    try {
        Invoke-Git -WorkingDirectory $RepositoryPath -Arguments @("rebase", "origin/main") | Out-Null
        Invoke-Git -WorkingDirectory $RepositoryPath -Arguments @("push", "origin", "HEAD:main") | Out-Null
    }
    catch {
        Invoke-Git -WorkingDirectory $RepositoryPath -Arguments @("rebase", "--abort") -AllowFailure | Out-Null
        throw
    }
}

function Reset-DedicatedScannerRuntimeChanges {
    param([string]$RepositoryPath)

    # A completed scan is already stored in a local commit before this runs.
    # Reset only uncommitted leftovers in the dedicated scanner clone so an
    # interrupted push can be safely rebased and retried on the next launch.
    Invoke-Git -WorkingDirectory $RepositoryPath -Arguments @("restore", "--staged", "--worktree", "--", ".") | Out-Null

    foreach ($RelativePath in @(
        "working\pipeline-checkpoint.json",
        "working\scan-progress.json"
    )) {
        $Target = Join-Path $RepositoryPath $RelativePath
        if (Test-Path -LiteralPath $Target) { Remove-Item -LiteralPath $Target -Force }
    }

    $CheckpointDirectory = Join-Path $RepositoryPath "working\checkpoints"
    if (Test-Path -LiteralPath $CheckpointDirectory) {
        Remove-Item -LiteralPath $CheckpointDirectory -Recurse -Force
    }

    $Remaining = (& git -C $RepositoryPath status --porcelain) -join "`n"
    if ($Remaining.Trim()) {
        Write-Host $Remaining
        throw "The dedicated scanner clone still has unexpected uncommitted files; recovery push was stopped."
    }
}

function Test-UsableScannerClone {
    param([string]$RepositoryPath)
    if (-not (Test-Path -LiteralPath (Join-Path $RepositoryPath ".git"))) { return $false }
    & git -C $RepositoryPath rev-parse --verify HEAD *> $null
    if ($LASTEXITCODE -ne 0) { return $false }
    & git -C $RepositoryPath rev-parse --verify refs/heads/main *> $null
    return ($LASTEXITCODE -eq 0)
}

function Move-IncompleteScannerClone {
    param([string]$RepositoryPath)
    if (-not (Test-Path -LiteralPath $RepositoryPath)) { return }
    $Timestamp = [DateTime]::Now.ToString("yyyyMMdd-HHmmss")
    $BackupPath = "$RepositoryPath-incomplete-$Timestamp"
    Move-Item -LiteralPath $RepositoryPath -Destination $BackupPath
    Write-Host "Incomplete download preserved at: $BackupPath" -ForegroundColor Yellow
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
    if (-not (Test-UsableScannerClone -RepositoryPath $ClonePath)) {
        Write-Host "Previous repository download is incomplete. Recovering automatically..." -ForegroundColor Yellow
        Move-IncompleteScannerClone -RepositoryPath $ClonePath
    }
}

if (-not (Test-Path -LiteralPath $ClonePath)) {
    Write-Host "Downloading latest GitHub files (fast shallow download)..." -ForegroundColor Cyan
    Invoke-Git -Arguments @(
        "-c", "http.lowSpeedLimit=1024",
        "-c", "http.lowSpeedTime=30",
        "clone", "--depth", "1", "--no-tags", "--branch", "main", "--single-branch",
        $RepositoryUrl, $ClonePath
    ) | Out-Null
}

# A previous interrupted push must never leave the next scan trapped in Git's
# detached rebase state. This affects only the dedicated scanner clone.
$RebaseMerge = Join-Path $ClonePath ".git\rebase-merge"
$RebaseApply = Join-Path $ClonePath ".git\rebase-apply"
if ((Test-Path -LiteralPath $RebaseMerge) -or (Test-Path -LiteralPath $RebaseApply)) {
    Write-Host "Recovering an interrupted previous Git rebase..." -ForegroundColor Yellow
    Invoke-Git -WorkingDirectory $ClonePath -Arguments @("rebase", "--abort") -AllowFailure | Out-Null
}

Invoke-Git -WorkingDirectory $ClonePath -Arguments @("fetch", "origin", "main") | Out-Null
Invoke-Git -WorkingDirectory $ClonePath -Arguments @("checkout", "main") | Out-Null
$PendingScanSubjects = @(& git -C $ClonePath log --format=%s origin/main..HEAD)
$RecoveredPendingScan = @(
    $PendingScanSubjects | Where-Object { $_ -like "Local auto update:*" }
).Count -gt 0

$NonScanPending = @(
    $PendingScanSubjects | Where-Object { $_ -notlike "Local auto update:*" }
)
if ($NonScanPending.Count) {
    throw "The dedicated scanner clone contains a non-scan local commit. No file was changed or pushed."
}

if ($RecoveredPendingScan) {
    Write-Host "Cleaning interrupted scan runtime files before recovery push..." -ForegroundColor Yellow
    Reset-DedicatedScannerRuntimeChanges -RepositoryPath $ClonePath
    Invoke-RebaseAndPush -RepositoryPath $ClonePath
    $RecoveredCommit = (& git -C $ClonePath rev-parse --short HEAD) -join ""
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "PREVIOUS COMPLETED SCAN WAS RECOVERED AND PUSHED" -ForegroundColor Green
    Write-Host "No duplicate scan was started. Commit: $RecoveredCommit"
    Write-Host "============================================================" -ForegroundColor Green
    exit 0
}

# Start from the exact latest GitHub code. Local scan never copies, commits or
# pushes edited code/config/site/workflow files.
Invoke-Git -WorkingDirectory $ClonePath -Arguments @("reset", "--hard", "origin/main") | Out-Null

$OldProgress = Join-Path $ClonePath "working\scan-progress.json"
if (Test-Path -LiteralPath $OldProgress) {
    Remove-Item -LiteralPath $OldProgress -Force
}

Invoke-Git -WorkingDirectory $ClonePath -Arguments @("config", "user.name", "Click TV Local Scanner") | Out-Null
Invoke-Git -WorkingDirectory $ClonePath -Arguments @("config", "user.email", "clicktv-local@users.noreply.github.com") | Out-Null

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

# All generated output is committed above. Clear every uncommitted scanner/test
# mutation before the clean check, while preserving the committed scan result.
Invoke-Git -WorkingDirectory $ClonePath -Arguments @("restore", "--staged", "--worktree", "--", ".") | Out-Null
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
Invoke-RebaseAndPush -RepositoryPath $ClonePath
$Commit = (& git -C $ClonePath rev-parse --short HEAD) -join ""

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "COMPLETED: $Mode SCAN + GITHUB PUSH" -ForegroundColor Green
Write-Host "Commit: $Commit"
Write-Host "Next time use this same Ruman-18 CMD file."
Write-Host "============================================================" -ForegroundColor Green
