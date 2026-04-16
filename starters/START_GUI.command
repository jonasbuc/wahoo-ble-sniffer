#!/bin/bash
# ════════════════════════════════════════════════════════════════
#  Wahoo Bridge GUI Monitor (macOS)
#  Double-click to open the live status window.
#  (Bridge must already be running.)
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

"$PYTHON" bridge/wahoo_bridge_gui.py --url ws://localhost:8765
