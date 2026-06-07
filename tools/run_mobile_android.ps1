param(
    [string]$DeviceId = 'emulator-5554',
    [string]$BackendUrl = 'http://10.0.2.2:8010',
    [switch]$StartBackend
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$mobileRoot = Join-Path $root 'mobile'

if ($StartBackend) {
    & (Join-Path $PSScriptRoot 'start_backend.ps1')
}

Write-Output "Starting GameVoice Android app on $DeviceId"
Write-Output "Backend URL: $BackendUrl"

Push-Location $mobileRoot
try {
    flutter run `
        -d $DeviceId `
        -t lib/main.dart `
        --dart-define=GAMEVOICE_BACKEND_URL=$BackendUrl
} finally {
    Pop-Location
}
