#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
#  Start System Check GUI server
# ──────────────────────────────────────────────────────────────
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

echo "🔍  Starting System Check GUI …"
cd "$REPO_ROOT"

# activate venv if present
if [[ -f ".venv/bin/activate" ]]; then
    source .venv/bin/activate
fi

python -m live_analytics.system_check.app
