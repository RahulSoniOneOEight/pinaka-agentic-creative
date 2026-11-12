# Full release gate, once per bundle. -Fast includes ALL tests on isolated workers.
param([switch]$Fast, [ValidateRange(1, 16)][int]$Workers = 4)
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $repo ".venv\Scripts\python.exe"
Push-Location $repo
try {
    & $python -m ruff check src tests scripts
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $python -m ruff format --check src tests scripts
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $pytestArgs = @("-m", "pytest", "-q")
    if ($Fast) {
        & $python -c "import xdist"
        if ($LASTEXITCODE -ne 0) { throw "Install the dev extra for parallel verification" }
        $pytestArgs += @("-n", "$Workers", "--dist", "loadfile")
    }
    & $python @pytestArgs
    exit $LASTEXITCODE
} finally { Pop-Location }
