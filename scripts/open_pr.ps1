param(
    [Parameter(Mandatory = $true)]
    [string]$Title,
    [string]$Base = "main",
    [string]$Head = "",
    [string]$BodyFile = ".github/pull_request_template.md",
    [switch]$EditCurrent
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI (gh) is required."
}

if (-not (Test-Path -LiteralPath $BodyFile)) {
    throw "Body file not found: $BodyFile"
}

if (-not $Head) {
    $Head = (git rev-parse --abbrev-ref HEAD).Trim()
}

if ($EditCurrent.IsPresent) {
    gh pr edit --title $Title --body-file $BodyFile
    exit $LASTEXITCODE
}

gh pr create --base $Base --head $Head --title $Title --body-file $BodyFile
exit $LASTEXITCODE
