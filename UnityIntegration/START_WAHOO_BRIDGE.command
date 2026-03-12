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
PYTHON="../.venv/bin/python"

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

# Start GUI in a new Terminal window and run the bridge in this window
# Use osascript to spawn a new Terminal tab/window that runs the GUI with --live
GUI_CMD="cd \"$(dirname "$0")\"; \"$PYTHON\" python/wahoo_bridge_gui.py --live"
osascript -e "tell application \"Terminal\" to do script \"$GUI_CMD\""

# Start canonical bridge (runs in this window) with --live
"$PYTHON" python/wahoo_unity_bridge.py --live

echo ""
echo "Bridge stopped."
read -p "Press Enter to close..."
