$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot "backend\venv\Scripts\python.exe"
$workerCount = if ($env:PYTEST_WORKERS) { [int]$env:PYTEST_WORKERS } else { 4 }
$baseTemp = Join-Path ([System.IO.Path]::GetTempPath()) "gettdone-pytest-dev-$PID"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment not found at $python"
}

try {
    & $python -m pytest -q -n $workerCount --dist=worksteal --basetemp $baseTemp @args
    $testExitCode = $LASTEXITCODE
}
finally {
    if (Test-Path -LiteralPath $baseTemp) {
        Remove-Item -Recurse -Force -LiteralPath $baseTemp
    }
}

exit $testExitCode
