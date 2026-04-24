<#
  Start the Questionnaire server  (FastAPI + uvicorn, port 8090)

  TIP: If PowerShell blocks this script with "running scripts is disabled",
  run ONE of these fixes (once per machine):
    • Double-click  live_analytics\questionnaire\scripts\run_questionnaire.bat  (recommended)
    • Or in an admin PowerShell:
        Set-ExecutionPolicy RemoteSigned -Scope CurrentUser -Force
#>
$ErrorActionPreference = "Stop"

# Resolve repo root: this script lives at <repo>/live_analytics/questionnaire/scripts/
$ScriptRoot  = Split-Path -Parent $MyInvocation.MyCommand.Path   # .../scripts
$QsDir       = Split-Path -Parent $ScriptRoot                     # .../questionnaire
$ProjectRoot = Split-Path -Parent $QsDir                          # .../live_analytics
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

$port = if ($env:QS_PORT) { $env:QS_PORT } else { "8090" }

Write-Host ""
Write-Host "=== Starting Questionnaire server ==="
Write-Host "  URL   : http://127.0.0.1:$port"
Write-Host "  Python: $VenvPython"
Write-Host ""

# CWD must be the repo root so package imports resolve correctly
Set-Location $RepoRoot

& $VenvPython -m live_analytics.questionnaire.app
