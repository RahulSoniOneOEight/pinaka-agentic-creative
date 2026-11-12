# Inner loop: check only what you changed, in seconds.
#
#   .\scripts\quick.ps1                                  # ruff on changed files, then last-failed tests
#   .\scripts\quick.ps1 -Tests tests/test_shot_motion.py   # ruff + one file
#   .\scripts\quick.ps1 -Keyword motion                    # ruff + tests matching a keyword
#   .\scripts\quick.ps1 -Tests tests/a.py,tests/b.py -Keyword veo
#
# The full suite belongs in .\scripts\check.ps1, run once before a commit.
param(
    [string[]]$Tests = @(),
    [string]$Keyword = "",
    [switch]$Full  # run every test, not just the last failures
)

$ErrorActionPreference = "Stop"
$python = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
$repo = Resolve-Path (Join-Path $PSScriptRoot "..")

# 1. Only the files touched since HEAD (+ untracked), so ruff never reflows the tree.
$changed = @(& git -c core.autocrlf=false -C $repo diff --name-only --diff-filter=ACMR HEAD)
$untracked = @(& git -c core.autocrlf=false -C $repo ls-files --others --exclude-standard)
$pythonFiles = @(@($changed) + @($untracked)) |
    Where-Object { $_ -match '\.py$' } |
    ForEach-Object { Join-Path $repo $_ }

if ($pythonFiles.Count -gt 0) {
    Write-Host "ruff: $($pythonFiles.Count) changed file(s)" -ForegroundColor Cyan
    & $python -m ruff check --fix $pythonFiles
    & $python -m ruff format $pythonFiles
    & $python -m ruff check $pythonFiles
} else {
    Write-Host "ruff: no changed Python files" -ForegroundColor DarkGray
}

# 2. Targeted tests only. Never the full suite here.
$pytestArgs = @("-m", "pytest", "-q", "-x", "--no-header")
if ($Tests.Count -gt 0) {
    $pytestArgs += $Tests
}
if ($Keyword) {
    $pytestArgs += @("-k", $Keyword)
}
if ($Tests.Count -eq 0 -and -not $Keyword -and -not $Full) {
    $pytestArgs += "--lf"   # last failed, so a red loop stays red until it is green
}

Write-Host "pytest: $($pytestArgs -join ' ')" -ForegroundColor Cyan
& $python @pytestArgs
