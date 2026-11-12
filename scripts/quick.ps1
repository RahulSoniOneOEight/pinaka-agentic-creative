# Targeted inner loop. No selection means cached failures only, never the full suite.
param(
    [string[]]$Tests = @(),
    [string]$Keyword = "",
    [switch]$Full,
    [switch]$IncludeSlow
)
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $repo ".venv\Scripts\python.exe"
Push-Location $repo
try {
    $changed = @(& git -c core.autocrlf=false diff --name-only --diff-filter=ACMR HEAD)
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $untracked = @(& git ls-files --others --exclude-standard)
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $pythonFiles = @(@($changed) + @($untracked) | Where-Object { $_ -match '\.py$' -and (Test-Path $_) } | Sort-Object -Unique)
    if ($pythonFiles.Count -gt 0) {
        & $python -m ruff check --fix @pythonFiles
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $python -m ruff format @pythonFiles
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $python -m ruff check @pythonFiles
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    $pytestArgs = @("-m", "pytest", "-q", "-x", "--no-header")
    if ($Tests.Count -gt 0) { $pytestArgs += $Tests }
    if ($Keyword) { $pytestArgs += @("-k", $Keyword) }
    if (-not $IncludeSlow -and -not $Full) { $pytestArgs += @("-m", "not slow") }
    $lastFailedOnly = $Tests.Count -eq 0 -and -not $Keyword -and -not $Full
    if ($lastFailedOnly) {
        $selectionJson = & $python (Join-Path $PSScriptRoot "failed_tests.py") $repo
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        # Windows PowerShell 5 wraps [] as one nested array unless explicitly flattened.
        $selectedIds = @($selectionJson | ConvertFrom-Json | ForEach-Object { $_ } | Where-Object { $_ -is [string] -and $_.Length -gt 0 })
        if ($selectedIds.Count -eq 0) {
            Write-Host "No current cached failures. Name -Tests or -Keyword; no tests were run."
            exit 0
        }
        $pytestArgs += $selectedIds
    }
    & $python @pytestArgs
    $result = $LASTEXITCODE
    if ($result -eq 5 -and $lastFailedOnly) {
        Write-Host "No cached failures selected. Name -Tests or -Keyword; no passing-test claim was made."
        exit 0
    }
    exit $result
} finally { Pop-Location }
