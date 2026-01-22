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
if ! $PYTHON -c "import bleak, websockets" 2>/dev/null; then
    echo "WARNING: Dependencies missing!"
    echo "Installing bleak and websockets..."
    pip install bleak websockets
fi

echo "OK: Dependencies installed"
echo ""
echo "Scanning for KICKR and TICKR..."
echo "TIP: Pedal to wake up your KICKR!"
echo ""
echo "WebSocket server starting on ws://localhost:8765"
echo ""
echo "════════════════════════════════════════════════════════════"
echo ""

# Start bridge
$PYTHON wahoo_unity_bridge.py

echo ""
echo "Bridge stopped."
read -p "Press Enter to close..."
