<# Live Analytics – start FastAPI + ingest server #>
$ErrorActionPreference = "Stop"

$ScriptRoot  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptRoot   # live_analytics/
$RepoRoot    = Split-Path -Parent $ProjectRoot   # repo root

# ── Locate venv Python ────────────────────────────────────────────────
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Error (
        "Virtual environment not found at: $VenvPython`n" +
        "Run INSTALL.bat first to create the environment."
    )
    exit 1
}

# Ensure data dirs and DB exist
& $VenvPython "$ScriptRoot\init_db.py"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Database initialisation failed (exit $LASTEXITCODE). Check the output above."
    exit 1
}

Write-Host ""
Write-Host "=== Starting Live Analytics server ==="
Write-Host "  HTTP API : http://127.0.0.1:$($env:LA_HTTP_PORT ?? '8080')"
Write-Host "  WS Ingest: ws://127.0.0.1:$($env:LA_WS_INGEST_PORT ?? '8766')"
Write-Host "  Python   : $VenvPython"
Write-Host ""

# Use venv Python explicitly — do NOT rely on PATH after activation,
# which may silently fall back to system Python if ExecutionPolicy blocks
# Activate.ps1.
Set-Location $RepoRoot
& $VenvPython -m live_analytics.app.main
