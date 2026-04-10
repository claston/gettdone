$ErrorActionPreference = "Stop"

Set-Location "$PSScriptRoot\..\frontend"

$venvPython = Join-Path $PSScriptRoot "..\backend\venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPython) {
    & $venvPython -m http.server 3000
}
elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python -m http.server 3000
}
elseif (Get-Command py -ErrorAction SilentlyContinue) {
    & py -m http.server 3000
}
else {
    throw "Python nao encontrado. Instale Python ou crie backend\\venv."
}
