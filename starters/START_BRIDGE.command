#!/bin/bash
# ════════════════════════════════════════════════════════════════
#  Wahoo BLE Bridge (macOS)
#  Double-click to start the real BLE bridge + GUI monitor.
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

echo ""
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║     🚴  Wahoo BLE Bridge                    ║"
echo "  ╚══════════════════════════════════════════════╝"
echo ""
echo "  Scanning for Wahoo BLE devices …"
echo "  WebSocket server → ws://localhost:8765"
echo ""

# Spawn GUI monitor in a new Terminal window once the bridge port is open
GUI_CMD="for i in {1..30}; do nc -z 127.0.0.1 8765 >/dev/null 2>&1 && break || sleep 1; done; cd '$(pwd)'; '$PYTHON' bridge/wahoo_bridge_gui.py --url ws://localhost:8765"
osascript -e "tell application \"Terminal\" to do script \"$GUI_CMD\""

# Start bridge in foreground
"$PYTHON" bridge/bike_bridge.py --live

echo ""
echo "  Bridge stoppet."
read -rp "  Tryk Enter for at lukke …"
