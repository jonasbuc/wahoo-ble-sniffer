<# Live Analytics – start Streamlit dashboard #>
$ErrorActionPreference = "Stop"

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptRoot

# Activate virtualenv if it exists
$venvActivate = Join-Path $ProjectRoot ".venv\Scripts\Activate.ps1"
if (Test-Path $venvActivate) {
    & $venvActivate
}

$DashboardScript = Join-Path $ProjectRoot "dashboard\streamlit_app.py"

Write-Host ""
Write-Host "=== Starting Live Analytics Dashboard ==="
Write-Host "  URL: http://127.0.0.1:$($env:LA_DASHBOARD_PORT ?? '8501')"
Write-Host ""

# CWD must be the REPO root (parent of live_analytics/) so that Streamlit
# finds .streamlit/config.toml which disables XSRF protection.
$RepoRoot = Split-Path -Parent $ProjectRoot
Set-Location $RepoRoot
streamlit run $DashboardScript --server.port $($env:LA_DASHBOARD_PORT ?? '8501')
