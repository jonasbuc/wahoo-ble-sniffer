#!/bin/bash
# Wahoo Mock Bridge Starter (macOS)
# Test without hardware - double-click this file!

cd "$(dirname "$0")"

echo "============================================================"
echo "  Wahoo MOCK Bridge (Test without hardware)"
echo "============================================================"
echo ""
echo "This is for testing/development without hardware!"
echo ""

# Find Python in virtual environment
# Find Python in virtual environment
PYTHON="../../.venv/bin/python"

if [ ! -f "$PYTHON" ]; then
    echo "WARNING: Virtual environment not found!"
    echo "Using system Python..."
    PYTHON="python3"
fi

# Ensure websockets is installed in the chosen interpreter
if ! $PYTHON -c "import websockets" 2>/dev/null; then
    echo "WARNING: Websockets missing! Installing into the chosen Python..."
    $PYTHON -m pip install websockets
fi

echo "OK: Dependencies installed"
echo ""
echo "Mock WebSocket server starting on ws://localhost:8765"
echo "Sending simulated cycling data..."
echo ""
echo "You can use this to develop your Unity game without"
echo "having to pedal constantly! :)"
echo ""
echo "════════════════════════════════════════════════════════════"
echo ""

# Start bridge in mock mode (no BLE hardware needed)
$PYTHON ../python/bike_bridge.py

echo ""
echo "Mock bridge stopped."
read -p "Press Enter to close..."
