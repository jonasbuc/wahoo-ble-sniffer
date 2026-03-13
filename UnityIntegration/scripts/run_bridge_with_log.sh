#!/usr/bin/env bash
# Run the bridge with unbuffered Python output and save to ~/bridge_live.log
# Usage: ./run_bridge_with_log.sh [--venv]

set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"

PYTHON=python3
if [ "${1:-}" = "--venv" ]; then
  if [ -x ".venv/bin/python" ]; then
    PYTHON=.venv/bin/python
  fi
fi

echo "Starting bridge with $PYTHON (stdout -> ~/bridge_live.log)"
"$PYTHON" -u python/wahoo_unity_bridge.py --live 2>&1 | tee ~/bridge_live.log
