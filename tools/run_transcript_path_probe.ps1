param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,

    [string]$OutputDir = "",

    [double]$ChunkSeconds = 0.04,

    [double]$SendDelaySeconds = -1,

    [string]$SpeakerContextId = ""
)

$repoRoot = Split-Path $PSScriptRoot -Parent
$backendSrc = Join-Path $repoRoot "backend\src"
if (-not $OutputDir) {
    $OutputDir = Join-Path $repoRoot ".runtime\transcript-path-probe"
}

$env:PYTHONPATH = $backendSrc
$env:PYTHONUTF8 = "1"

$args = @(
    "-m", "gamevoice_server.transcript_path_probe",
    "--input", $InputPath,
    "--output-dir", $OutputDir,
    "--chunk-seconds", $ChunkSeconds
)

if ($SendDelaySeconds -ge 0) {
    $args += @("--send-delay-seconds", $SendDelaySeconds)
}

if ($SpeakerContextId) {
    $args += @("--speaker-context-id", $SpeakerContextId)
}

python @args
