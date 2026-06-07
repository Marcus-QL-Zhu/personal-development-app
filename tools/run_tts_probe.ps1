param(
    [string]$Text = "TTS probe. First sentence. Second sentence."
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repoRoot "backend"
$srcRoot = Join-Path $backendRoot "src"

$env:PYTHONPATH = $srcRoot
python -m gamevoice_server.tts_probe --text "$Text"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
