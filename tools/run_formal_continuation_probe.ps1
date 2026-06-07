param(
    [string]$Transcript = "player_a: explain sanguosha rules",
    [string]$PreviewText = "Sanguosha rules quick guide."
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repoRoot "backend"
$srcRoot = Join-Path $backendRoot "src"

$env:PYTHONPATH = $srcRoot
python -m gamevoice_server.formal_continuation_probe --transcript "$Transcript" --preview-text "$PreviewText"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
