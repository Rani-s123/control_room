# Control Room — Windows PowerShell Demo Launcher
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$env:DEMO_MODE = "1"
if (-not $env:DEMO_ROWS) { $env:DEMO_ROWS = "250000" }
if (-not $env:DEMO_SCENARIO) { $env:DEMO_SCENARIO = "shield_eviction" }

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
    Write-Host "==> created .venv" -ForegroundColor DarkGray
}
$py = ".venv\Scripts\python.exe"

Write-Host "==> installing dependencies" -ForegroundColor DarkGray
& $py -m pip install -q chdb numpy fastapi "uvicorn[standard]" Pillow

Write-Host "==> Launching Demo Mode: Embedded ClickHouse ($env:DEMO_ROWS events, scenario: $env:DEMO_SCENARIO)" -ForegroundColor Cyan
Write-Host "==> Open http://localhost:8080 in your browser" -ForegroundColor Green

& $py -m uvicorn controlroom.server:app --host 0.0.0.0 --port 8080
