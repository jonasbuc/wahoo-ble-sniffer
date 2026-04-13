# ──────────────────────────────────────────────────────────────
#  Start the Questionnaire server (FastAPI + uvicorn)
#  Default: http://localhost:8090
# ──────────────────────────────────────────────────────────────
$QsDir = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$QsDir = Join-Path $QsDir "live_analytics" "questionnaire"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $QsDir)

Push-Location $QsDir

# Activate venv if present
$venv = Join-Path $RepoRoot ".venv" "Scripts" "Activate.ps1"
if (Test-Path $venv) { & $venv }

$port = if ($env:QS_PORT) { $env:QS_PORT } else { "8090" }
Write-Host "🚀 Starting Questionnaire server on http://localhost:$port"
python -m uvicorn app:app --host 0.0.0.0 --port $port --reload

Pop-Location
