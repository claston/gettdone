$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$hooksPath = ".githooks"

Push-Location $repoRoot
try {
    git config core.hooksPath $hooksPath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to configure git hooks path."
    }
    Write-Host "Git hooks path configured: $hooksPath"
    Write-Host "Pre-commit hook enabled."
    Write-Host "Tip: run lint manually with: backend\\venv\\Scripts\\python.exe scripts\\lint_frontend_text.py"
}
finally {
    Pop-Location
}
