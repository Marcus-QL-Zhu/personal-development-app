$ErrorActionPreference = "Continue"
Write-Host "=== Checking Environment Variables ==="
Write-Host "MINIMAX_API_KEY length: $env:MINIMAX_API_KEY.Length"
Write-Host "MINIMAX_API_KEY first 10 chars: $env:MINIMAX_API_KEY".Substring(0, [Math]::Min(10, $env:MINIMAX_API_KEY.Length))
Write-Host "METASO_API_KEY length: $env:METASO_API_KEY.Length"
Write-Host "METASO_API_KEY first 10 chars: $env:METASO_API_KEY".Substring(0, [Math]::Min(10, $env:METASO_API_KEY.Length))
Write-Host ""

$key = $env:MINIMAX_API_KEY
Write-Host "Testing Python with env var passed directly..."
$testScript = @"
import os
import sys
key = os.environ.get('MINIMAX_API_KEY', '')
print(f'KEY_LEN={len(key)}')
print(f'KEY={key[:10]}...' if len(key) > 10 else f'KEY={key}')
sys.stdout.flush()
"@

$env:MINIMAX_API_KEY = $key
$result = python -c $testScript 2>&1
Write-Host "Result: $result"
