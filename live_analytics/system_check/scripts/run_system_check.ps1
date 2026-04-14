<# ──────────────────────────────────────────────────────────────
#  Start System Check GUI server  (PowerShell)
# ────────────────────────────────────────────────────────────── #>
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = (Resolve-Path "$ScriptDir\..\..\..").Path

Write-Host "🔍  Starting System Check GUI …" -ForegroundColor Cyan
Set-Location $RepoRoot

# activate venv if present
if (Test-Path ".venv\Scripts\Activate.ps1") {
    & ".venv\Scripts\Activate.ps1"
}

python -m live_analytics.system_check.app
