# Prompts for the two secrets and writes them into .env at the right keys.
# Nothing is echoed, nothing is logged, and the key names are never touched -
# which is what went wrong when the replacement was typed by hand.

$ErrorActionPreference = "Stop"
$envPath = Join-Path $PSScriptRoot "..\.env" | Resolve-Path

Write-Host ""
Write-Host "  Do cheezein maangunga. Paste karke Enter dabaiye." -ForegroundColor Cyan
Write-Host ""

$p = Read-Host "  1/2  ClickHouse password"
if ([string]::IsNullOrWhiteSpace($p)) { Write-Host "  Khali chhod diya - ruk raha hoon." -ForegroundColor Red; exit 1 }

$k = Read-Host "  2/2  Gemini API key"
if ([string]::IsNullOrWhiteSpace($k)) { Write-Host "  Khali chhod diya - ruk raha hoon." -ForegroundColor Red; exit 1 }

$lines = Get-Content $envPath
$out = foreach ($line in $lines) {
    if     ($line -match '^CLICKHOUSE_PASSWORD=') { "CLICKHOUSE_PASSWORD=$($p.Trim())" }
    elseif ($line -match '^GOOGLE_API_KEY=')     { "GOOGLE_API_KEY=$($k.Trim())" }
    else                                          { $line }
}
Set-Content -Path $envPath -Value $out

Write-Host ""
Write-Host "  Ho gaya. .env bhar diya gaya." -ForegroundColor Green
foreach ($name in @("CLICKHOUSE_HOST","CLICKHOUSE_PASSWORD","GOOGLE_API_KEY")) {
    $v = (Get-Content $envPath | Where-Object { $_ -match "^$name=" }) -replace "^$name=", ""
    $status = if ($v -eq "FILL_ME" -or [string]::IsNullOrWhiteSpace($v)) { "KHALI" } else { "OK ($($v.Length) chars)" }
    Write-Host ("    {0,-22} {1}" -f $name, $status)
}
Write-Host ""
