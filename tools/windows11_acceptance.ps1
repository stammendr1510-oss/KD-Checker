<#
.SYNOPSIS
    KD-PE-106 acceptance run for Windows 11.

.DESCRIPTION
    Reproducible, offline-capable acceptance script for the KD Checker candidate
    commit. It deploys into a SEPARATE directory, verifies the exact commit SHA,
    creates an isolated virtual environment, runs the dependency policy, the
    targeted KD-PE-105 tests, the full pytest suite, the synthetic acceptance
    stand, and finally starts the application twice to prove that it can be
    launched, opened and stopped cleanly.

    It never touches the existing working directories
    C:\KD-Checker\work, C:\KD-Checker\codex, C:\KD-Checker\integration or
    C:\KD-Checker\coordination, never merges and never pushes.

    Every step writes a log into <WorkDir>\logs. A machine-readable summary is
    written to <WorkDir>\windows11-acceptance-summary.json.

    Exit code: 0 only when every executed step passed. Any failure returns 1 and
    the summary keeps the failing step visible. A step that could not be run is
    reported as NOT_VERIFIED and also returns 1: Windows 11 acceptance is never
    inferred from a skipped step.

.PARAMETER RepoUrl
    Git repository to deploy from.

.PARAMETER ExpectedSha
    Exact commit that must be checked out. Mismatch stops the run.

.PARAMETER WorkDir
    Separate acceptance directory. Must not exist or must be empty.

.PARAMETER PythonCommand
    Python launcher command, for example "py -3.13" or "python".

.PARAMETER SkipClone
    Reuse an existing checkout in WorkDir (SHA is still verified).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\windows11_acceptance.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\windows11_acceptance.ps1 `
        -WorkDir C:\KD-Checker-acceptance -PythonCommand "py -3.13"
#>
[CmdletBinding()]
param(
    [string]$RepoUrl = "https://github.com/stammendr1510-oss/KD-Checker.git",
    [string]$ExpectedSha = "1d1b603cae14f2adba8c043e85404e239aae4d96",
    [string]$WorkDir = "C:\KD-Checker-acceptance",
    [string]$PythonCommand = "py -3.13",
    [switch]$SkipClone
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$results = New-Object System.Collections.Generic.List[object]
$logs = Join-Path $WorkDir "logs"
$summaryPath = Join-Path $WorkDir "windows11-acceptance-summary.json"
$appProcess = $null

function Write-StepResult {
    param([string]$Name, [string]$Status, [string]$Detail, [string]$Log)
    $entry = [ordered]@{
        step    = $Name
        status  = $Status
        detail  = $Detail
        log     = $Log
        at_utc  = (Get-Date).ToUniversalTime().ToString("o")
    }
    $results.Add([pscustomobject]$entry)
    Write-Host ("[{0}] {1} - {2}" -f $Status, $Name, $Detail)
}

function Invoke-LoggedStep {
    param(
        [string]$Name,
        [scriptblock]$Action,
        [switch]$AllowFailure
    )
    $log = Join-Path $logs ("{0}.log" -f ($Name -replace '[^A-Za-z0-9_.-]', '_'))
    try {
        $output = & $Action 2>&1
        $output | Tee-Object -FilePath $log -Append | Out-Host
        if ($LASTEXITCODE -ne 0 -and -not $AllowFailure) {
            throw "exit code $LASTEXITCODE"
        }
        Write-StepResult -Name $Name -Status "PASS" -Detail "completed" -Log $log
        return $true
    }
    catch {
        $message = $_.Exception.Message
        ("ERROR: {0}" -f $message) | Tee-Object -FilePath $log -Append | Out-Host
        if ($AllowFailure) {
            Write-StepResult -Name $Name -Status "NOT_VERIFIED" -Detail $message -Log $log
        }
        else {
            Write-StepResult -Name $Name -Status "FAIL" -Detail $message -Log $log
        }
        return $false
    }
}

function Invoke-Python {
    param([string]$Arguments, [string]$WorkDirectory = $WorkDir)
    Push-Location $WorkDirectory
    try {
        $parts = $PythonCommand.Split(" ")
        $exe = $parts[0]
        $prefix = @()
        if ($parts.Length -gt 1) { $prefix = $parts[1..($parts.Length - 1)] }
        & $exe @prefix @($Arguments.Split(" ")) | Out-Host
        return $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}

function Wait-ForEndpoint {
    param([string]$Url, [int]$TimeoutSeconds = 60)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
            if ($response.StatusCode -eq 200) { return $true }
        }
        catch {
            Start-Sleep -Seconds 2
        }
    }
    return $false
}

try {
    New-Item -ItemType Directory -Path $WorkDir -Force | Out-Null
    New-Item -ItemType Directory -Path $logs -Force | Out-Null

    Write-StepResult -Name "environment" -Status "INFO" `
        -Detail ("PowerShell {0}, {1}" -f $PSVersionTable.PSVersion, [System.Environment]::OSVersion.VersionString) `
        -Log $logs

    if (-not $SkipClone) {
        $existing = Get-ChildItem -Path $WorkDir -Force | Where-Object { $_.Name -ne "logs" -and $_.Name -ne "windows11-acceptance-summary.json" }
        if ($existing) {
            throw "WorkDir is not empty: $WorkDir. Use a separate directory or -SkipClone."
        }
        $ok = Invoke-LoggedStep -Name "git_clone" -Action {
            Push-Location $WorkDir
            try {
                & git clone --no-checkout $RepoUrl .
                & git fetch --all --prune
            }
            finally { Pop-Location }
        }
        if (-not $ok) { throw "git clone failed" }
    }

    $ok = Invoke-LoggedStep -Name "git_checkout_exact_sha" -Action {
        Push-Location $WorkDir
        try {
            & git cat-file -e ("{0}^{commit}" -f $ExpectedSha)
            if ($LASTEXITCODE -ne 0) { throw "commit $ExpectedSha is not present" }
            & git checkout --detach $ExpectedSha
            $actual = (& git rev-parse HEAD).Trim()
            if ($actual -ne $ExpectedSha) { throw "HEAD $actual != expected $ExpectedSha" }
            & git status --short
        }
        finally { Pop-Location }
    }
    if (-not $ok) { throw "exact SHA checkout failed" }

    $ok = Invoke-LoggedStep -Name "python_venv" -Action {
        Push-Location $WorkDir
        try {
            Invoke-Python -Arguments "-m venv .venv"
            if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
            & .\.venv\Scripts\python.exe -m pip install --upgrade pip
            & .\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
            if ($LASTEXITCODE -ne 0) { throw "dependency installation failed" }
            & .\.venv\Scripts\python.exe --version
        }
        finally { Pop-Location }
    }
    if (-not $ok) { throw "environment setup failed" }

    Invoke-LoggedStep -Name "dependency_policy" -Action {
        Push-Location $WorkDir
        try { & .\.venv\Scripts\python.exe tools\dependency_policy.py }
        finally { Pop-Location }
    } | Out-Null

    Invoke-LoggedStep -Name "targeted_kd_pe_105_tests" -Action {
        Push-Location $WorkDir
        try {
            & .\.venv\Scripts\python.exe -m pytest -q `
                tests\test_identifier_classification.py `
                tests\test_kd_pe_105_confidence_model.py `
                tests\test_kd_pe_105_evidence.py `
                tests\test_e3_pe3_row_matching.py `
                tests\test_kd_pe_105_diagnostics.py `
                tests\test_e3_pe3_inventory.py `
                tests\test_e3_pe3_details.py
        }
        finally { Pop-Location }
    } | Out-Null

    Invoke-LoggedStep -Name "full_pytest" -Action {
        Push-Location $WorkDir
        try { & .\.venv\Scripts\python.exe -m pytest -q }
        finally { Pop-Location }
    } | Out-Null

    Invoke-LoggedStep -Name "engineering_acceptance_stand" -Action {
        Push-Location $WorkDir
        try {
            & .\.venv\Scripts\python.exe acceptance_stand.py --json acceptance-report.json
            if ($LASTEXITCODE -ne 0) { throw "acceptance stand returned $LASTEXITCODE" }
        }
        finally { Pop-Location }
    } | Out-Null

    Invoke-LoggedStep -Name "synthetic_stand_accept" -Action {
        Push-Location $WorkDir
        try {
            & .\.venv\Scripts\python.exe synthetic_stand.py accept --synthetic-only --workspace .\acceptance-stand
            if ($LASTEXITCODE -ne 0) { throw "synthetic stand returned $LASTEXITCODE" }
        }
        finally { Pop-Location }
    } | Out-Null

    # Two launches prove that the application starts, serves the UI and stops
    # cleanly. The second launch uses the same isolated directory on purpose.
    foreach ($attempt in 1..2) {
        $name = "application_launch_$attempt"
        $log = Join-Path $logs ("{0}.log" -f $name)
        try {
            $appProcess = Start-Process -FilePath "cmd.exe" `
                -ArgumentList "/c start_windows.bat" `
                -WorkingDirectory $WorkDir -PassThru `
                -RedirectStandardOutput $log -RedirectStandardError "$log.err"
            if (-not (Wait-ForEndpoint -Url "http://127.0.0.1:8765" -TimeoutSeconds 90)) {
                throw "http://127.0.0.1:8765 did not answer within 90 seconds"
            }
            $page = Invoke-WebRequest -Uri "http://127.0.0.1:8765" -UseBasicParsing -TimeoutSec 10
            if ($page.Content -notmatch "Проверка КД") {
                throw "UI page does not contain the expected title"
            }
            Write-StepResult -Name $name -Status "PASS" -Detail "UI reachable, page loaded" -Log $log
        }
        catch {
            Write-StepResult -Name $name -Status "FAIL" -Detail $_.Exception.Message -Log $log
        }
        finally {
            if ($appProcess -and -not $appProcess.HasExited) {
                Stop-Process -Id $appProcess.Id -Force -ErrorAction SilentlyContinue
            }
            Get-Process -Name python -ErrorAction SilentlyContinue |
                Where-Object { $_.Path -like "$WorkDir*" } |
                Stop-Process -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 3
        }
    }
}
catch {
    Write-StepResult -Name "fatal" -Status "FAIL" -Detail $_.Exception.Message -Log $logs
}
finally {
    if ($appProcess -and -not $appProcess.HasExited) {
        Stop-Process -Id $appProcess.Id -Force -ErrorAction SilentlyContinue
    }
    $failed = @($results | Where-Object { $_.status -eq "FAIL" })
    $unverified = @($results | Where-Object { $_.status -eq "NOT_VERIFIED" })
    $verdict = if ($failed.Count -gt 0) { "FAIL" } elseif ($unverified.Count -gt 0) { "NOT_VERIFIED" } else { "PASS" }
    $summary = [ordered]@{
        kind              = "kd-checker-windows11-acceptance-v1"
        verdict           = $verdict
        expected_sha      = $ExpectedSha
        work_dir          = $WorkDir
        platform          = [System.Environment]::OSVersion.VersionString
        powershell        = $PSVersionTable.PSVersion.ToString()
        python_command    = $PythonCommand
        generated_at      = (Get-Date).ToUniversalTime().ToString("o")
        failed_steps      = $failed.Count
        not_verified_steps = $unverified.Count
        steps             = $results
        note              = "Windows 11 acceptance is valid only for the exact expected_sha above."
    }
    $summary | ConvertTo-Json -Depth 6 | Set-Content -Path $summaryPath -Encoding UTF8
    Write-Host ""
    Write-Host ("Windows 11 acceptance verdict: {0}" -f $verdict)
    Write-Host ("Summary: {0}" -f $summaryPath)
    if ($verdict -ne "PASS") { exit 1 }
    exit 0
}
