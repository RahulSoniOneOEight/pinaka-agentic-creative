# Pre-commit gate. Run once per change bundle, not per edit.
#
#   .\scripts\check.ps1          # ruff (all), format check, full pytest
#   .\scripts\check.ps1 -Fast    # same, but pytest -n auto if pytest-xdist is installed
param(
    [switch]$Fast,
    [int]$Workers = 6
)

$ErrorActionPreference = "Stop"
$python = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
$repo = Resolve-Path (Join-Path $PSScriptRoot "..")

Write-Host "ruff check" -ForegroundColor Cyan
& $python -m ruff check (Join-Path $repo "src") (Join-Path $repo "tests")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "ruff format --check" -ForegroundColor Cyan
& $python -m ruff format --check (Join-Path $repo "src") (Join-Path $repo "tests")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "pytest" -ForegroundColor Cyan
$pytestArgs = @("-m", "pytest", "-q")
if ($Fast) {
    $hasXdist = & $python -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('xdist') else 1)"
    if ($LASTEXITCODE -eq 0) {
        $pytestArgs += @("-n", "$Workers")
    } else {
        Write-Host "pytest-xdist not installed; running serially" -ForegroundColor Yellow
    }
}
& $python @pytestArgs
