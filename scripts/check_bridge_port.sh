#!/usr/bin/env bash
# Quick environment check: is the bridge listening and which processes may be related to Bluetooth
set -euo pipefail

echo "Checking TCP 8765 listener..."
if lsof -nP -iTCP:8765 -sTCP:LISTEN >/dev/null 2>&1; then
  lsof -nP -iTCP:8765 -sTCP:LISTEN
else
  echo "No process is listening on TCP:8765"
fi

echo "\nProcesses that commonly interact with Bluetooth (sharingd, identityservicesd, bluetoothd):"
ps aux | egrep 'sharingd|identityservicesd|bluetoothd' | egrep -v egrep || true

echo "\nYou can stream macOS bluetooth logs with (requires sudo):"
echo "sudo log stream --predicate 'subsystem == \"com.apple.bluetooth\"' --style syslog"
