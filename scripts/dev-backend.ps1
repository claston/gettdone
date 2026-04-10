$ErrorActionPreference = "Stop"

Set-Location "$PSScriptRoot\..\backend"

if (-not (Test-Path -LiteralPath "venv\Scripts\python.exe")) {
    throw "Virtual environment not found at backend\venv. Create it first."
}

venv\Scripts\python.exe -m uvicorn app.main:app --reload
