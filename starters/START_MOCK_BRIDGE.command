#!/bin/bash
# ════════════════════════════════════════════════════════════════
#  Wahoo MOCK Bridge (macOS)
#  Double-click to start simulated cycling data + GUI monitor.
#  No BLE hardware needed!
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
echo "  ║     🚴  Wahoo MOCK Bridge (test)            ║"
echo "  ╚══════════════════════════════════════════════╝"
echo ""
echo "  Sender simuleret cykeldata …"
echo "  WebSocket server → ws://localhost:8765"
echo ""

# Spawn GUI monitor in a new Terminal window once the bridge port is open
GUI_CMD="for i in {1..30}; do nc -z 127.0.0.1 8765 >/dev/null 2>&1 && break || sleep 1; done; cd '$(pwd)'; '$PYTHON' UnityIntegration/python/wahoo_bridge_gui.py --url ws://localhost:8765"
osascript -e "tell application \"Terminal\" to do script \"$GUI_CMD\""

# Start mock bridge in foreground (no BLE hardware needed)
"$PYTHON" UnityIntegration/python/bike_bridge.py

echo ""
echo "  Mock bridge stoppet."
read -rp "  Tryk Enter for at lukke …"
