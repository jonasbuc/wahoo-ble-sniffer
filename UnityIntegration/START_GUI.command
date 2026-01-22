#!/bin/bash
# Wahoo Bridge GUI Launcher (macOS)
# Double-click to open status monitor!

cd "$(dirname "$0")"

# Find Python
PYTHON="../.venv/bin/python"

if [ ! -f "$PYTHON" ]; then
    PYTHON="python3"
fi

# Launch GUI
$PYTHON wahoo_bridge_gui.py
