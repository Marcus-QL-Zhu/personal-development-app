param(
    [int]$BackendPort = 8010,
    [int]$WebPort = 7357,
    [switch]$UseRealBackend
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$backendUrl = "http://localhost:$BackendPort"

if ($UseRealBackend) {
    throw 'Use tools/run_mobile_android.ps1 -StartBackend for real backend/native audio. Browser test env uses the demo repository.'
}

Write-Output 'Using browser demo repository; backend startup skipped.'

& (Join-Path $PSScriptRoot 'start_mobile_web.ps1') `
    -Port $WebPort `
    -BackendUrl $backendUrl
