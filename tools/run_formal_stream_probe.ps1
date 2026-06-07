param(
    [string]$Transcript = "player_a: explain sanguosha rules",
    [string]$PreviewText = "Sanguosha is a role-based card game set in the Three Kingdoms era.",
    [double]$InterSentenceDelayMs = 250
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repoRoot "backend"
$srcRoot = Join-Path $backendRoot "src"

$env:PYTHONPATH = $srcRoot
python -m gamevoice_server.formal_stream_probe --transcript "$Transcript" --preview-text "$PreviewText" --inter-sentence-delay-ms $InterSentenceDelayMs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
