param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,
    [string]$OutputPath = "",
    [string]$PreviousSummary = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repoRoot "backend"
$srcRoot = Join-Path $backendRoot "src"
if (-not $OutputPath) {
    $OutputPath = Join-Path $repoRoot ".runtime\memory-compaction-probe-output.txt"
}

$env:PYTHONPATH = $srcRoot
$args = @(
    "-m"
    "gamevoice_server.memory_compaction_probe"
    "--input"
    $InputPath
    "--output"
    $OutputPath
)

if ($PreviousSummary -ne "") {
    $args += @("--previous-summary", $PreviousSummary)
}

python @args
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
