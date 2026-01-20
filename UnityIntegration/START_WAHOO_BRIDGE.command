#!/bin/bash
# Wahoo Unity Bridge Starter (macOS)
# Dobbeltklik på denne fil for at starte bridge'en!

cd "$(dirname "$0")"

echo "============================================================"
echo "  🚴‍♂️ Wahoo BLE to Unity Bridge"
echo "============================================================"
echo ""
echo "Starting Python bridge..."
echo ""

# Find Python i virtual environment
PYTHON="../.venv/bin/python"

if [ ! -f "$PYTHON" ]; then
    echo "⚠️  Virtual environment ikke fundet!"
    echo "Installer dependencies først:"
    echo "  cd 'Blu Sniffer'"
    echo "  pip install bleak websockets"
    echo ""
    read -p "Tryk Enter for at lukke..."
    exit 1
fi

# Tjek om dependencies er installeret
if ! $PYTHON -c "import bleak, websockets" 2>/dev/null; then
    echo "⚠️  Dependencies mangler!"
    echo "Installerer bleak og websockets..."
    pip install bleak websockets
fi

echo "✓ Dependencies OK"
echo ""
echo "🔍 Scanner efter KICKR og TICKR..."
echo "💡 Tips: Træd på pedalerne for at vække KICKR!"
echo ""
echo "🌐 WebSocket server starter på ws://localhost:8765"
echo ""
echo "════════════════════════════════════════════════════════════"
echo ""

# Start bridge
$PYTHON wahoo_unity_bridge.py

echo ""
echo "Bridge stoppet."
read -p "Tryk Enter for at lukke..."
