$ErrorActionPreference = 'Stop'

$baseUrl = 'http://127.0.0.1:8010'
$sampleFile = Join-Path (Split-Path -Parent $PSScriptRoot) 'mobile\README.md'

$health = Invoke-RestMethod -Uri "$baseUrl/health"
$table = Invoke-RestMethod -Method Post -Uri "$baseUrl/tables" -ContentType 'application/json' -Body '{"name":"Smoke Table","origin":"test"}'
$upload = curl.exe -s -X POST -F "files=@$sampleFile" "$baseUrl/tables/$($table.id)/documents"
$summary = Invoke-RestMethod -Uri "$baseUrl/tables/$($table.id)/documents/README/read?mode=summary"

[PSCustomObject]@{
    health = $health.status
    table = $table.name
    upload = $upload
    summary = $summary.content
} | ConvertTo-Json -Depth 4
