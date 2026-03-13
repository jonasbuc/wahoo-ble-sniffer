#!/usr/bin/env python3
"""
find_wahoo_devices.py — Deep BLE device scanner
================================================
Scans for every reachable BLE device, then briefly connects to each one to
inspect its GATT service table.  Devices that expose a Heart Rate, Fitness
Machine, or Cycling Power service are reported as TICKR or KICKR candidates.

This is a *deep* scan: it establishes a real GATT connection to every device
it finds, which is slower (~15-20 s) but definitive.  Use quick_find.py when
you just need the address fast.

Usage:
  python find_wahoo_devices.py
"""

import asyncio
from bleak import BleakScanner, BleakClient

# ── GATT Service UUIDs we look for ───────────────────────────────────────────
# These are the standard Bluetooth-assigned 128-bit UUIDs for fitness profiles.
HEART_RATE_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"   # Wahoo TICKR
FITNESS_MACHINE_SERVICE = "00001826-0000-1000-8000-00805f9b34fb"  # KICKR (FTMS)
CYCLING_POWER_SERVICE = "00001818-0000-1000-8000-00805f9b34fb"    # KICKR (older firmware)


async def find_devices():
    """Scan, connect to each device, and report which ones expose fitness services.

    For every discovered device we attempt a temporary BleakClient connection
    (5-second timeout). If the connection succeeds we read the GATT service
    list and check for the UUIDs above. Devices that don't expose any of the
    three services, or that we can't connect to, are silently skipped.

    At the end a summary is printed together with the exact CLI command needed
    to start the logger with the discovered addresses.
    """
    print("Scanning for BLE devices with fitness services...")
    print("This may take 15-20 seconds...\n")

    # Passive scan — collects all advertising BLE devices within range
    devices = await BleakScanner.discover(timeout=15)

    # We'll sort found devices into these two buckets
    tickr_candidates = []
    kickr_candidates = []

    for device in devices:
        # Try to connect briefly and check services.
        # We use a context-manager (async with) so the connection is always
        # closed even if service enumeration raises an exception.
        try:
            async with BleakClient(device, timeout=5) as client:
                services = client.services
                # Normalise all UUIDs to lower-case for comparison
                service_uuids = [s.uuid.lower() for s in services]

                has_hr    = HEART_RATE_SERVICE    in service_uuids  # TICKR exposes this
                has_ftms  = FITNESS_MACHINE_SERVICE in service_uuids  # KICKR (FTMS)
                has_power = CYCLING_POWER_SERVICE  in service_uuids  # KICKR (older)

                if has_hr or has_ftms or has_power:
                    name = device.name if device.name else "(Unknown)"
                    print(f"\n{'='*60}")
                    print(f"Device: {name}")
                    print(f"Address: {device.address}")

                    if has_hr:
                        print("  ✓ Heart Rate Service (0x180D) - LIKELY TICKR")
                        tickr_candidates.append(device)

                    if has_ftms:
                        print("  ✓ Fitness Machine Service (0x1826) - LIKELY KICKR")
                        kickr_candidates.append(device)

                    if has_power:
                        print("  ✓ Cycling Power Service (0x1818) - LIKELY KICKR")
                        kickr_candidates.append(device)

                    # Dump the full service list for further debugging
                    print("\nAll services:")
                    for service in services:
                        print(f"  - {service.uuid}: {service.description}")

        except Exception:
            # Many BLE devices (headphones, phones, etc.) will refuse our connection
            # or time out — that's expected, just skip them.
            pass

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("\nSUMMARY:")
    print(f"\nTICKR Candidates ({len(tickr_candidates)}):")
    for dev in tickr_candidates:
        name = dev.name if dev.name else "(Unknown)"
        print(f"  {name} - {dev.address}")

    print(f"\nKICKR Candidates ({len(kickr_candidates)}):")
    for dev in kickr_candidates:
        name = dev.name if dev.name else "(Unknown)"
        print(f"  {name} - {dev.address}")

    if tickr_candidates or kickr_candidates:
        # Print the ready-to-use command so the user can copy-paste it
        print("\n" + "="*60)
        print("RUN THIS COMMAND:")
        tickr_addr = tickr_candidates[0].address if tickr_candidates else "NONE"
        kickr_addr = kickr_candidates[0].address if kickr_candidates else "NONE"
        print(f"\npython wahoo_ble_logger.py --tickr-address {tickr_addr} --kickr-address {kickr_addr}")
        print("="*60)


if __name__ == "__main__":
    asyncio.run(find_devices())
