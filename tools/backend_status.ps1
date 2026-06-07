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
    $listenerPids = Get-ListeningPids $port
    if ($listenerPids) {
        $pidList = ($listenerPids | ForEach-Object { "$_" }) -join ', '
        Write-Output "No pid file found, but port $port is already in use by PID(s): $pidList."
        exit 1
    }
    Write-Output 'Backend is not running (no pid file).'
    exit 1
}

$backendPid = Get-Content $pidPath -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $backendPid) {
    Write-Output 'Backend pid file is empty.'
    exit 1
}

$process = Get-Process -Id $backendPid -ErrorAction SilentlyContinue
if (-not $process) {
    Write-Output "Backend pid $backendPid is not alive."
    exit 1
}

$portListener = Get-ListeningPids $port | Where-Object { $_ -eq "$backendPid" } | Select-Object -First 1

Write-Output "Backend running with PID $backendPid"
if ($portListener) {
    Write-Output "Port $port is listening."
} else {
    Write-Output "Process exists but port $port is not listening. The pid file is stale or the backend failed after startup."
    exit 1
}
