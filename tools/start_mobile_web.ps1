param(
    [int]$Port = 7357,
    [string]$BackendUrl = 'http://localhost:8010',
    [switch]$UseRealBackend
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$mobileRoot = Join-Path $root 'mobile'
$useDemo = if ($UseRealBackend) { 'false' } else { 'true' }

if ($UseRealBackend) {
    throw 'Flutter Web real-backend mode needs a web HTTP transport. Use demo mode in the browser, or tools/run_mobile_android.ps1 for real backend/native audio.'
}

Write-Output "Starting GameVoice Flutter Web on http://127.0.0.1:$Port"
Write-Output "Backend URL: $BackendUrl"
Write-Output "Demo repository: $useDemo"
Write-Output ''

Push-Location $mobileRoot
try {
    flutter run `
        -d chrome `
        --web-hostname 127.0.0.1 `
        --web-port $Port `
        -t lib/main_local.dart `
        --dart-define=GAMEVOICE_BACKEND_URL=$BackendUrl `
        --dart-define=GAMEVOICE_USE_DEMO_REPOSITORY=$useDemo
} finally {
    Pop-Location
}
