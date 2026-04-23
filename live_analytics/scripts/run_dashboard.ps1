<#
  Live Analytics – start Streamlit dashboard

  TIP: If PowerShell blocks this script with "running scripts is disabled",
  run ONE of these fixes (once per machine):
    • Double-click  live_analytics\scripts\run_dashboard.bat  (recommended)
    • Or in an admin PowerShell:
        Set-ExecutionPolicy RemoteSigned -Scope CurrentUser -Force
#>
$ErrorActionPreference = "Stop"

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptRoot   # live_analytics/
$RepoRoot    = Split-Path -Parent $ProjectRoot   # repo root

# ── Locate venv Python ────────────────────────────────────────────────
$VenvPython  = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Error (
        "Virtual environment not found at: $VenvPython`n" +
        "Run INSTALL.bat first to create the environment."
    )
    exit 1
}

$DashboardScript = Join-Path $ProjectRoot "dashboard\streamlit_app.py"
if (-not (Test-Path $DashboardScript)) {
    Write-Error "Dashboard script not found: $DashboardScript"
    exit 1
}

Write-Host ""
Write-Host "=== Starting Live Analytics Dashboard ==="
Write-Host "  URL: http://127.0.0.1:$($env:LA_DASHBOARD_PORT ?? '8501')"
Write-Host "  Script: $DashboardScript"
Write-Host "  Python: $VenvPython"
Write-Host ""

# CWD must be the REPO root so Streamlit finds .streamlit/config.toml
Set-Location $RepoRoot

# Use venv Python explicitly — do NOT rely on PATH after activation,
# which may silently fall back to system Python if ExecutionPolicy blocks
# Activate.ps1.
& $VenvPython -m streamlit run $DashboardScript `
    --server.port $($env:LA_DASHBOARD_PORT ?? '8501') `
    --server.headless true `
    --browser.gatherUsageStats false
