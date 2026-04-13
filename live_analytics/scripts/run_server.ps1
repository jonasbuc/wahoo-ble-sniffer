<# Live Analytics – start FastAPI + ingest server #>
$ErrorActionPreference = "Stop"

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptRoot

# Activate virtualenv if it exists
$venvActivate = Join-Path $ProjectRoot ".venv\Scripts\Activate.ps1"
if (Test-Path $venvActivate) {
    & $venvActivate
}

# Ensure data dirs exist
python "$ScriptRoot\init_db.py"

Write-Host ""
Write-Host "=== Starting Live Analytics server ==="
Write-Host "  HTTP API : http://127.0.0.1:$($env:LA_HTTP_PORT ?? '8080')"
Write-Host "  WS Ingest: ws://127.0.0.1:$($env:LA_WS_INGEST_PORT ?? '8765')/ws/ingest"
Write-Host ""

# Start the server
Set-Location $ProjectRoot
python -m live_analytics.app.main
