#!/bin/bash
# ════════════════════════════════════════════════════════════════
#  Bike VR – Start All Services (macOS)
#  Double-click to start everything!
# ════════════════════════════════════════════════════════════════

cd "$(dirname "$0")/.."

PYTHON=".venv/bin/python"

if [ ! -f "$PYTHON" ]; then
    echo ""
    echo "  ✗  Virtual environment ikke fundet!"
    echo "     Kør INSTALL.command først."
    echo ""
    read -rp "  Tryk Enter for at lukke …"
    exit 1
fi

exec "$PYTHON" starters/launcher.py "$@"
