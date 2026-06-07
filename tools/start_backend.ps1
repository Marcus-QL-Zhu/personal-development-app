$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$pythonPath = if ($env:GAMEVOICE_PYTHON) { $env:GAMEVOICE_PYTHON } else { 'python' }
$hostAddress = '0.0.0.0'
$port = 8010
$runtimeDir = Join-Path $root '.runtime'
$stdoutPath = Join-Path $runtimeDir 'backend.stdout.log'
$stderrPath = Join-Path $runtimeDir 'backend.stderr.log'
$pidPath = Join-Path $runtimeDir 'backend.pid'
$commandPath = Join-Path $runtimeDir 'run_backend.cmd'

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

if (-not (Test-Path $runtimeDir)) {
    New-Item -ItemType Directory -Path $runtimeDir | Out-Null
}

if (Test-Path $pidPath) {
    $existingPid = (Get-Content $pidPath -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($existingPid) {
        $existingProcess = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
        $existingListener = Get-ListeningPids $port | Where-Object { $_ -eq "$existingPid" } | Select-Object -First 1
        if ($existingProcess -and $existingListener) {
            Write-Output "Backend already running with PID $existingPid"
            exit 0
        }
    }
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
}

$portListeners = Get-ListeningPids $port
if ($portListeners) {
    $pidList = ($portListeners | ForEach-Object { "$_" }) -join ', '
    throw "Port $port is already in use by PID(s): $pidList. Stop the existing listener or use stop_backend.ps1 if it is the local GameVoice backend."
}

$command = @"
@echo off
cd /d "$root"
"$pythonPath" -m uvicorn --app-dir backend/src gamevoice_server.main:app --host $hostAddress --port $port 1>>"$stdoutPath" 2>>"$stderrPath"
"@
Set-Content -LiteralPath $commandPath -Value $command -Encoding ASCII

$launcher = Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', 'start', '""', '/b', $commandPath) -PassThru -WindowStyle Hidden
$backendPid = $null

for ($attempt = 0; $attempt -lt 20; $attempt++) {
    Start-Sleep -Milliseconds 500
    $listenerPid = Get-ListeningPids $port | Select-Object -First 1
    if ($listenerPid) {
        $backendPid = $listenerPid
        break
    }
}

if (-not $backendPid) {
    throw "Backend failed to start on port $port. Check $stderrPath"
}

Set-Content -LiteralPath $pidPath -Value $backendPid

$lanIps = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object {
        $_.IPAddress -notlike '127.*' -and
        $_.IPAddress -notlike '169.254.*'
    } |
    Select-Object -ExpandProperty IPAddress -Unique

Write-Output 'Backend started'
Write-Output "PID: $backendPid"
Write-Output "Emulator URL: http://10.0.2.2:$port"
foreach ($ip in $lanIps) {
    Write-Output "LAN URL: http://${ip}:$port"
}
Write-Output 'Logs:'
Write-Output "  STDOUT: $stdoutPath"
Write-Output "  STDERR: $stderrPath"
