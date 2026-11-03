$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot "backend\venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment not found at $python"
}

& $python (Join-Path $repoRoot "scripts\test_backend.py") @args
exit $LASTEXITCODE
