$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$pidPath = Join-Path $root '.runtime\backend.pid'
$port = 8010

function Get-ListeningPids([int]$TargetPort) {
    $matches = netstat -ano | Select-String -Pattern "[:\.]$TargetPort\s+.*LISTENING\s+(\d+)$"
    $pids = @()
    foreach ($match in $matches) {
        if ($match.Matches.Count -gt 0) {
            $pids += $match.Matches[0].Groups[1].Value
        }
    }
    return $pids | Select-Object -Unique
}

if (-not (Test-Path $pidPath)) {
    Write-Output 'Backend is not running.'
    exit 0
}

$backendPid = Get-Content $pidPath -ErrorAction SilentlyContinue | Select-Object -First 1
if ($backendPid) {
    Stop-Process -Id $backendPid -Force -ErrorAction SilentlyContinue
}

$listenerPids = Get-ListeningPids $port
foreach ($listenerPid in $listenerPids) {
    Stop-Process -Id $listenerPid -Force -ErrorAction SilentlyContinue
}

Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
Write-Output 'Backend stopped.'
