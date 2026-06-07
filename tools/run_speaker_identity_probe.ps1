param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,
    [string]$OutputDir = "",
    [double]$ChunkSeconds = 1.0
)

$repoRoot = Split-Path $PSScriptRoot -Parent
$backendSrc = Join-Path $repoRoot "backend\src"
if (-not $OutputDir) {
    $OutputDir = Join-Path $repoRoot ".runtime\speaker-identity-probe"
}
$env:PYTHONUTF8 = "1"
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$backendSrc;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = $backendSrc
}

python -m gamevoice_server.speaker_identity_probe `
    --input $InputPath `
    --output-dir $OutputDir `
    --chunk-seconds $ChunkSeconds
