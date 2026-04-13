#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
#  Start the Questionnaire server (FastAPI + uvicorn)
#  Default: http://localhost:8090
# ──────────────────────────────────────────────────────────────
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
QS_DIR="$REPO_ROOT/live_analytics/questionnaire"

cd "$QS_DIR"

# Activate venv if present
if [ -f "$REPO_ROOT/.venv/bin/activate" ]; then
    source "$REPO_ROOT/.venv/bin/activate"
fi

echo "🚀 Starting Questionnaire server on http://localhost:${QS_PORT:-8090}"
python -m uvicorn app:app --host 0.0.0.0 --port "${QS_PORT:-8090}" --reload
