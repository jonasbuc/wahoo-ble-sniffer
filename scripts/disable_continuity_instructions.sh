#!/usr/bin/env bash
# Helper: print safe instructions for temporarily reducing macOS Continuity/advertising activity.
# NOTE: These commands are suggestions — many Continuity features are global and managed in System Settings.
# Prefer using the UI to toggle AirDrop, Handoff, Nearby Sharing and related features.

cat <<'EOF'
Temporary Continuity/Advertise mitigation - safe steps to try (manual):

1) Turn off AirDrop (UI):
   Finder -> Go -> AirDrop -> "Allow me to be discovered by" -> No One

2) Turn off Handoff (UI):
   System Settings -> General -> AirDrop & Handoff (or General -> Handoff) -> toggle Handoff OFF

3) Turn off Nearby/Continuity features (UI):
   System Settings -> General -> Nearby Interaction / Sharing -> disable features like AirPlay, Continuity Camera, Universal Clipboard, etc.

4) If you prefer CLI (advanced, reversible) you can disable Handoff using defaults (requires logout/login):
   defaults write ~/Library/Preferences/ByHost/com.apple.coreservices.useractivityd ActivityAdvertisingEnabled -bool NO
   defaults write ~/Library/Preferences/ByHost/com.apple.coreservices.useractivityd ActivityReceivingEnabled -bool NO
   # Then reboot or log out/in for the change to take full effect.

5) To stop some advertising daemons temporarily (not recommended for general use):
   # These are system daemons - quitting them may affect macOS features. Prefer UI toggles.
   sudo launchctl kickstart -k system/com.apple.sharingd   # restart sharingd (AirDrop)

6) After testing, revert CLI Handoff change:
   defaults write ~/Library/Preferences/ByHost/com.apple.coreservices.useractivityd ActivityAdvertisingEnabled -bool YES
   defaults write ~/Library/Preferences/ByHost/com.apple.coreservices.useractivityd ActivityReceivingEnabled -bool YES
   # then logout/login

Safety note: modifying system defaults or killing system daemons can be disruptive. Use UI toggles when possible.

Recommended test flow:
 - Start the bridge and bluetooth log capture (use capture_pair_logs.sh)
 - Toggle AirDrop/Handoff OFF
 - Reproduce your HR disconnect scenario for several minutes
 - Re-enable AirDrop/Handoff and retest

EOF
