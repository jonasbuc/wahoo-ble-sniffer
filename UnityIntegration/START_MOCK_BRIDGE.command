#!/bin/bash
# Wahoo Mock Bridge Starter (macOS)
# Test uden hardware - dobbeltklik på denne fil!

cd "$(dirname "$0")"

echo "============================================================"
echo "  🎮 Wahoo MOCK Bridge (Test uden hardware)"
echo "============================================================"
echo ""
echo "Dette er til test/udvikling uden KICKR!"
echo ""

# Find Python i virtual environment
PYTHON="../.venv/bin/python"

if [ ! -f "$PYTHON" ]; then
    echo "⚠️  Virtual environment ikke fundet!"
    echo "Bruger system Python..."
    PYTHON="python3"
fi

# Tjek om dependencies er installeret
if ! $PYTHON -c "import websockets" 2>/dev/null; then
    echo "⚠️  Websockets mangler!"
    echo "Installerer websockets..."
    pip install websockets
fi

echo "✓ Dependencies OK"
echo ""
echo "🌐 Mock WebSocket server starter på ws://localhost:8765"
echo "📊 Sender simulerede cykeldata..."
echo ""
echo "Dette kan bruges til at udvikle Unity spillet uden at"
echo "skulle træde konstant på cyklen! 😄"
echo ""
echo "════════════════════════════════════════════════════════════"
echo ""

# Start mock bridge
$PYTHON mock_wahoo_bridge.py

echo ""
echo "Mock bridge stoppet."
read -p "Tryk Enter for at lukke..."
