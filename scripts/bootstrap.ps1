# Control Room — Windows PowerShell Bootstrap Script
$ErrorActionPreference = "Stop"

if (Test-Path .env) {
    Get-Content .env | ForEach-Object {
        if ($_ -and -not $_.StartsWith("#") -and $_.Contains("=")) {
            $name, $value = $_.Split("=", 2)
            [Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim(), "Process")
        }
    }
} else {
    Write-Host "WARNING: .env not found. Copy .env.example to .env and configure ClickHouse credentials for cloud mode." -ForegroundColor Yellow
}

Write-Host "==> Installing dependencies" -ForegroundColor Cyan
python -m pip install -q -r requirements.txt

Write-Host "==> Creating schema and seeding telemetry" -ForegroundColor Cyan
python data/generate_events.py --rows 250000 --window-min 180

Write-Host "==> Control Room ready on http://localhost:8080" -ForegroundColor Green
python -m uvicorn controlroom.server:app --host 0.0.0.0 --port 8080
