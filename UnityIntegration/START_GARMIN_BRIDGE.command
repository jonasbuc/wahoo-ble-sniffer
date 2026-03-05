#!/bin/bash
# Garmin Speed Sensor Bridge Launcher (macOS)
# Connects Garmin Speed Sensor 2 to Unity

cd "$(dirname "$0")"

echo "╔═══════════════════════════════════════════════════════════╗"
echo "║                                                           ║"
echo "║        GARMIN SPEED SENSOR → UNITY BRIDGE                ║"
echo "║                                                           ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    echo "❌ ERROR: Python 3 not found!"
    echo ""
    echo "Please install Python from:"
    echo "https://www.python.org/downloads/"
    echo ""
    read -p "Press Enter to exit..."
    exit 1
fi

# Check if venv exists
if [ ! -d "../.venv" ]; then
    echo "❌ ERROR: Python environment not installed!"
    echo ""
    echo "Please run INSTALL.command first"
    echo ""
    read -p "Press Enter to exit..."
    exit 1
fi

# Activate venv
source ../.venv/bin/activate

# Check dependencies
echo "🔍 Checking dependencies..."
if ! python3 -c "import bleak, websockets" 2>/dev/null; then
    echo "❌ ERROR: Required packages not installed!"
    echo ""
    echo "Please run INSTALL.command first"
    echo ""
    read -p "Press Enter to exit..."
    exit 1
fi

echo "✅ Dependencies OK"
echo ""

echo "📡 INSTRUCTIONS:"
echo ""
echo "1. Wake up your Garmin Speed Sensor 2:"
echo "   • Spin the wheel or move the sensor"
echo "   • LED should blink red/green"
echo ""
echo "2. Keep Unity ready with BikeMovementController"
echo ""
echo "3. Bridge will auto-connect when sensor is active"
echo ""
echo "Press Ctrl+C to stop"
echo ""
echo "═══════════════════════════════════════════════════════════"
echo ""

# Start the bridge
python3 python/wahoo_unity_bridge.py

# Keep terminal open on error
if [ $? -ne 0 ]; then
    echo ""
    echo "❌ Bridge stopped with error"
    read -p "Press Enter to exit..."
fi
