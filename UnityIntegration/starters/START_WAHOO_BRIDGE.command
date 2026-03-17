#!/bin/bash
# Wahoo Unity Bridge Starter (macOS)
# Double-click this file to start the bridge!

cd "$(dirname "$0")"

echo "============================================================"
echo "  Wahoo BLE to Unity Bridge"
echo "============================================================"
echo ""
echo "Starting Python bridge..."
echo ""

# Find Python in virtual environment
PYTHON="../../.venv/bin/python"

if [ ! -f "$PYTHON" ]; then
    echo "WARNING: Virtual environment not found!"
    echo "Install dependencies first:"
    echo "  cd 'Blu Sniffer'"
    echo "  pip install bleak websockets"
    echo ""
    read -p "Press Enter to close..."
    exit 1
fi

# Check if dependencies are installed
# Use the venv pip if we need to install
if ! $PYTHON -c "import bleak, websockets" 2>/dev/null; then
    echo "WARNING: Dependencies missing!"
    echo "Installing bleak and websockets into the virtualenv..."
    $PYTHON -m pip install bleak websockets
fi

echo "OK: Dependencies installed"
echo ""
echo "WebSocket server starting on ws://localhost:8765"
echo ""
echo "════════════════════════════════════════════════════════════"
echo ""

# Start the bridge first, then launch the GUI monitor
# We'll run the bridge in this window and spawn the GUI in a new Terminal window

# Spawn GUI in a new Terminal window after waiting for the bridge port (8765)
# Replace the fixed sleep with a small TCP poll loop (30s max) so the GUI only
# launches after the bridge begins listening. This avoids racey startup.
GUI_CMD="for i in {1..30}; do nc -z 127.0.0.1 8765 >/dev/null 2>&1 && break || sleep 1; done; cd '$(dirname "$0")'; '$PYTHON' ../python/wahoo_bridge_gui.py --url ws://localhost:8765"
osascript -e "tell application \"Terminal\" to do script \"$GUI_CMD\""

# Start canonical bridge in the current window (foreground)
"$PYTHON" ../python/bike_bridge.py --live

echo ""
echo "Bridge stopped."
read -p "Press Enter to close..."
