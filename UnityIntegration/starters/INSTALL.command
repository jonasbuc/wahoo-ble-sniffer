#!/bin/bash
# Wahoo Bridge Auto-Installer (macOS)
# Double-click to install everything automatically!

cd "$(dirname "$0")/.."

echo "============================================================"
echo "  Wahoo Bridge - Auto Installer"
echo "============================================================"
echo ""
echo "This will install everything you need!"
echo ""

# Check Python version
echo "[1/5] Checking Python..."
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 not found!"
    echo "Please install Python from: https://www.python.org/downloads/"
    read -p "Press Enter to close..."
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d' ' -f2 | cut -d'.' -f1,2)
echo "OK: Found Python $PYTHON_VERSION"
echo ""

# Create virtual environment
echo "[2/5] Creating virtual environment..."
if [ -d ".venv" ]; then
    echo "OK: Virtual environment already exists"
else
    python3 -m venv .venv
    echo "OK: Virtual environment created"
fi
echo ""

# Activate and install dependencies
echo "[3/5] Installing dependencies..."
source .venv/bin/activate

pip install --quiet --upgrade pip
pip install --quiet bleak websockets

echo "OK: Dependencies installed"
echo ""

# Verify installation
echo "[4/5] Verifying installation..."
if python -c "import bleak, websockets" 2>/dev/null; then
    echo "OK: All packages verified"
else
    echo "ERROR: Installation verification failed!"
    read -p "Press Enter to close..."
    exit 1
fi
echo ""

# Make starter scripts executable
echo "[5/5] Setting up starter scripts..."
cd UnityIntegration
chmod +x START_WAHOO_BRIDGE.command
chmod +x START_MOCK_BRIDGE.command
chmod +x wahoo_bridge_gui.command 2>/dev/null
echo "OK: Starter scripts ready"
echo ""

echo "============================================================"
echo "  INSTALLATION COMPLETE!"
echo "============================================================"
echo ""
echo "Next steps:"
echo "1. Go to UnityIntegration folder"
echo "2. Double-click START_WAHOO_BRIDGE.command"
echo "3. Start Unity and connect!"
echo ""
echo "Happy cycling! :)"
echo ""
read -p "Press Enter to close..."
