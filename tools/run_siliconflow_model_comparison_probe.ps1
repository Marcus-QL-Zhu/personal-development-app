param(
    [string]$ApiKey = $env:SILICONFLOW_API_KEY,
    [string]$OutputDir = ".runtime/siliconflow-model-comparison"
)

$ErrorActionPreference = "Stop"

if (-not $ApiKey) {
    Write-Host "[ERROR] SILICONFLOW_API_KEY environment variable is not set." -ForegroundColor Red
    exit 1
}

Add-Type -AssemblyName System.Web

$Url = "https://api.siliconflow.cn/v1/chat/completions"

$SystemPrompt = "你是一个桌游助手，名字叫小美，现在开始介绍你自己，只能说一句话"
$UserPrompt = "你是一个桌游助手，名字叫小美，现在开始介绍你自己，只能说一句话"

$Models = @(
    @{ name = "Qwen/Q3.5-4B";               model = "Qwen/Qwen3.5-4B" },
    @{ name = "inclusionAI/Ling-mini-2.0";  model = "inclusionAI/Ling-mini-2.0" }
)

$OutputPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDir)
if (-not (Test-Path $OutputPath)) {
    New-Item -ItemType Directory -Path $OutputPath | Out-Null
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Results = @()

foreach ($m in $Models) {
    Write-Host "`n========================================" -ForegroundColor Cyan
    Write-Host "Testing: $($m.name)" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan

    $body = @{
        model     = $m.model
        messages  = @(
            @{ role = "system"; content = $SystemPrompt },
            @{ role = "user";   content = $UserPrompt }
        )
        max_tokens        = 50
        temperature       = 0.45
        top_p             = 0.8
        top_k             = 40
        frequency_penalty = 0.2
        stream            = $false
        enable_thinking   = $false
        n                 = 1
    }

    $jsonBody = [System.Text.Encoding]::UTF8.GetBytes(
        ($body | ConvertTo-Json -Compress)
    )

    $t0 = Get-Date

    try {
        # WebClient gives us raw bytes — no PowerShell codepage mangling
        $wc = [System.Net.WebClient]::new()
        $wc.Headers["Authorization"] = "Bearer $ApiKey"
        $wc.Headers["Content-Type"]  = "application/json; charset=utf-8"
        $rawBytes = $wc.UploadData($Url, "POST", $jsonBody)
        $wc.Dispose()

        $elapsed  = ((Get-Date) - $t0).TotalSeconds
        $responseText = [System.Text.Encoding]::UTF8.GetString($rawBytes)
        $reply   = ($responseText | ConvertFrom-Json).choices[0].message.content

        Write-Host "[OK] Elapsed: $([math]::Round($elapsed, 3))s" -ForegroundColor Green
        Write-Host "Reply: $reply" -ForegroundColor Yellow

        $Results += @{
            model     = $m.name
            elapsed_s = [math]::Round($elapsed, 3)
            reply     = $reply
            success   = $true
            error     = $null
        }
    }
    catch {
        $elapsed = ((Get-Date) - $t0).TotalSeconds
        Write-Host "[FAIL] Elapsed: $([math]::Round($elapsed, 3))s" -ForegroundColor Red
        Write-Host "Error: $_" -ForegroundColor Red
        if ($wc) { $wc.Dispose() }

        $Results += @{
            model     = $m.name
            elapsed_s = [math]::Round($elapsed, 3)
            reply     = $null
            success   = $false
            error     = $_.Exception.Message
        }
    }
}

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "SUMMARY" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

foreach ($r in $Results) {
    $status = if ($r.success) { "OK" } else { "FAIL" }
    $color  = if ($r.success) { "Green" } else { "Red" }
    Write-Host "[$status] $($r.model): $($r.elapsed_s)s" -ForegroundColor $color
    if ($r.reply) { Write-Host "      Reply: $($r.reply)" -ForegroundColor Yellow }
    if ($r.error) { Write-Host "      Error: $($r.error)" -ForegroundColor Red }
}

$summaryPath = Join-Path $OutputPath "comparison-$timestamp.json"
$Results | ConvertTo-Json -Depth 5 | Set-Content -Path $summaryPath -Encoding UTF8
Write-Host "`nResults saved to: $summaryPath" -ForegroundColor DarkGray
