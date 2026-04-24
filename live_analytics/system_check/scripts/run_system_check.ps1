<#
  Start System Check GUI server  (FastAPI + uvicorn, port 8095)

  TIP: If PowerShell blocks this script with "running scripts is disabled",
  run ONE of these fixes (once per machine):
    • Double-click  live_analytics\system_check\scripts\run_system_check.bat  (recommended)
    • Or in an admin PowerShell:
        Set-ExecutionPolicy RemoteSigned -Scope CurrentUser -Force
#>
$ErrorActionPreference = "Stop"

# Resolve repo root: this script lives at <repo>/live_analytics/system_check/scripts/
$ScriptRoot  = Split-Path -Parent $MyInvocation.MyCommand.Path   # .../scripts
$ScDir       = Split-Path -Parent $ScriptRoot                     # .../system_check
$ProjectRoot = Split-Path -Parent $ScDir                          # .../live_analytics
$RepoRoot    = Split-Path -Parent $ProjectRoot                    # repo root

# ── Locate venv Python ────────────────────────────────────────────────
# Use explicit venv Python — do NOT rely on Activate.ps1 which may be
# blocked by ExecutionPolicy on a clean Windows machine.
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Error (
        "Virtual environment not found at: $VenvPython`n" +
        "Run starters\INSTALL.bat first to create the environment."
    )
    exit 1
}

$port = if ($env:SC_PORT) { $env:SC_PORT } else { "8095" }

Write-Host ""
Write-Host "=== Starting System Check GUI ==="
Write-Host "  URL   : http://127.0.0.1:$port"
Write-Host "  Python: $VenvPython"
Write-Host ""

# CWD must be the repo root so package imports resolve correctly
Set-Location $RepoRoot

& $VenvPython -m live_analytics.system_check.app
