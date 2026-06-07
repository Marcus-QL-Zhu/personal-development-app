param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,

    [string]$OutputDir = ".runtime/speaker-live-probe",

    [double]$ChunkSeconds = 1.0
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repoRoot "backend"
$srcRoot = Join-Path $backendRoot "src"
$ffmpegPackagesRoot = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
$ffmpegBin = Get-ChildItem -Path $ffmpegPackagesRoot -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty DirectoryName

$env:PYTHONPATH = $srcRoot
$env:PATH = if ($ffmpegBin) { "$ffmpegBin;$env:PATH" } else { $env:PATH }
python -m gamevoice_server.speaker_live_probe --input "$InputPath" --output-dir "$OutputDir" --chunk-seconds $ChunkSeconds
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
