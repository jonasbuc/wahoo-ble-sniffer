#!/usr/bin/env bash
# Capture bridge stdout/stderr and macOS bluetoothd logs to files for reproducible traces.
# Usage: run this in a terminal and reproduce the disconnect while it runs.
# Press Enter in this terminal to stop capturing.

set -euo pipefail

# Create capture directory inside the repository working directory so the logs
# are accessible to local tools and this agent. This avoids writing to $HOME
# which may be outside the workspace and not readable by the agent.
OUT_DIR="$(pwd)/wahoo_bridge_capture_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUT_DIR"
BRIDGE_LOG="$OUT_DIR/bridge_live.log"
BLUETOOTH_LOG="$OUT_DIR/bluetoothd.log"

echo "Capture directory: $OUT_DIR"

# Start bridge in background (unbuffered)
if [ -x "../.venv/bin/python" ]; then
  PY="../.venv/bin/python"
else
  PY="python3"
fi

echo "Starting bridge with: $PY -u UnityIntegration/python/wahoo_unity_bridge.py --live"
# Start bridge in background and capture its PID. Use setsid to keep it separate from this terminal.
setsid "$PY" -u UnityIntegration/python/wahoo_unity_bridge.py --live > "$BRIDGE_LOG" 2>&1 &
BRIDGE_PID=$!
sleep 0.5
if ps -p $BRIDGE_PID > /dev/null 2>&1; then
  echo "Bridge started (pid $BRIDGE_PID) -> $BRIDGE_LOG"
else
  echo "Bridge failed to start; check $BRIDGE_LOG"
  exit 1
fi

# Start bluetoothd log streaming (requires sudo)
echo "About to start sudo log stream for bluetooth. You will be prompted for your password." 
sudo sh -c "log stream --predicate 'subsystem == \"com.apple.bluetooth\"' --style syslog > '$BLUETOOTH_LOG' 2>&1 & echo \$! > '$OUT_DIR/bluetoothd.pid'"
BLUETOOTH_PID=$(cat "$OUT_DIR/bluetoothd.pid")

echo "Bluetooth log streaming started (pid $BLUETOOTH_PID) -> $BLUETOOTH_LOG"

echo "\nNow reproduce the disconnect or run your scenario." 
read -p "When finished reproducing, press Enter to stop captures..."

echo "Stopping bluetooth log stream (sudo) and bridge..."
if [ -n "$BLUETOOTH_PID" ] && ps -p $BLUETOOTH_PID > /dev/null 2>&1; then
  sudo kill $BLUETOOTH_PID || true
fi

if ps -p $BRIDGE_PID > /dev/null 2>&1; then
  kill $BRIDGE_PID || true
fi

echo "Captures saved in: $OUT_DIR"
ls -l "$OUT_DIR"

echo "You can now attach or paste the following files for analysis:"
echo "  $BRIDGE_LOG"
echo "  $BLUETOOTH_LOG"

echo "Done."
