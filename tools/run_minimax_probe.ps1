param(
    [int]$Repeats = 3
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repoRoot "backend"
$srcRoot = Join-Path $backendRoot "src"

$env:PYTHONPATH = $srcRoot
python -m gamevoice_server.minimax_probe --repeats $Repeats
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
