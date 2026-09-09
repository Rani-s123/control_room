# Control Room - Windows PowerShell bootstrap.
#
# Mirrors scripts/bootstrap.sh: load .env, install, CREATE THE SCHEMA, seed
# telemetry, serve. The schema step is not optional against a fresh ClickHouse
# Cloud service - data/generate_events.py inserts straight into
# control_room.playback_events and fails if that table does not exist yet.
#
#   .\scripts\bootstrap.ps1                  # 5,000,000 rows (matches README)
#   .\scripts\bootstrap.ps1 -Rows 1000000    # smaller, for a slow uplink
#   .\scripts\bootstrap.ps1 -SkipSchema      # re-seed a service already set up
#   .\scripts\bootstrap.ps1 -NoServe         # load only, do not start uvicorn

param(
    [int]$Rows = 5000000,
    [switch]$SkipSchema,
    [switch]$NoServe
)

$ErrorActionPreference = "Stop"

if (Test-Path .env) {
    Get-Content .env | ForEach-Object {
        if ($_ -and -not $_.StartsWith("#") -and $_.Contains("=")) {
            $name, $value = $_.Split("=", 2)
            [Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim(), "Process")
        }
    }
} else {
    Write-Host "ERROR: .env not found." -ForegroundColor Red
    Write-Host "       Run:  copy .env.example .env" -ForegroundColor Red
    Write-Host "       Then fill in CLICKHOUSE_HOST and CLICKHOUSE_PASSWORD." -ForegroundColor Red
    exit 1
}

if (-not $env:CLICKHOUSE_HOST) {
    Write-Host "ERROR: CLICKHOUSE_HOST is empty in .env." -ForegroundColor Red
    Write-Host "       Without it the app falls back to embedded demo mode," -ForegroundColor Red
    Write-Host "       and the ClickHouse track requirement is not met." -ForegroundColor Red
    exit 1
}

if ($env:CLICKHOUSE_TRANSPORT) {
    Write-Host "WARNING: CLICKHOUSE_TRANSPORT is set to '$($env:CLICKHOUSE_TRANSPORT)'." -ForegroundColor Yellow
    Write-Host "         Comment it out in .env - leaving it unset is what selects" -ForegroundColor Yellow
    Write-Host "         the official MCP transport, which is requirement #1." -ForegroundColor Yellow
}

Write-Host "==> Installing dependencies" -ForegroundColor Cyan
python -m pip install -q -r requirements.txt

if (-not $SkipSchema) {
    Write-Host "==> Creating schema on $($env:CLICKHOUSE_HOST)" -ForegroundColor Cyan
    python scripts/create_schema.py
}

Write-Host "==> Loading telemetry ($('{0:N0}' -f $Rows) events)" -ForegroundColor Cyan
python data/generate_events.py --rows $Rows --window-min 180

if ($NoServe) {
    Write-Host "==> Done. Server not started (-NoServe)." -ForegroundColor Green
    exit 0
}

Write-Host ""
Write-Host "==> Serving on http://localhost:8080" -ForegroundColor Green
Write-Host "    Now open http://localhost:8080/healthz" -ForegroundColor Green
Write-Host "    transport MUST read 'mcp'. If it reads 'embedded', .env did not load." -ForegroundColor Green
Write-Host ""
python -m uvicorn controlroom.server:app --host 0.0.0.0 --port 8080
