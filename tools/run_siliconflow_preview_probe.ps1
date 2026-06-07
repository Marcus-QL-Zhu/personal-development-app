param(
    [string]$Transcript = "宝子，三国杀反贼到底怎么赢？",
    [ValidateSet("chatty", "serious")]
    [string]$Mode = "serious",
    [string]$OutputDir = ".runtime/siliconflow-preview-probe",
    [double]$TimeoutSeconds = 30
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not $env:SILICONFLOW_API_KEY) {
    throw "SILICONFLOW_API_KEY is not set in the current PowerShell environment."
}

$env:PYTHONPATH = Join-Path $repoRoot "backend\src"
python -m gamevoice_server.siliconflow_preview_probe `
    --transcript "$Transcript" `
    --mode "$Mode" `
    --output-dir "$OutputDir" `
    --timeout-seconds "$TimeoutSeconds"
