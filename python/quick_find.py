#!/usr/bin/env python3
"""
quick_find.py — Fast parallel BLE device discovery
====================================================
Scans for BLE devices and probes up to 20 of them *concurrently* using
``asyncio.gather``.  Each probe opens a short GATT connection (3-second
timeout) and checks whether the device exposes a Heart Rate or Fitness
Machine service.

This is the fast alternative to ``find_wahoo_devices.py``.  It sacrifices
the full service dump for speed — ideal when you already know roughly which
devices are around and just need their Bluetooth addresses.

Usage:
  python quick_find.py
"""

import asyncio
from bleak import BleakScanner, BleakClient

# ── GATT Service UUIDs ───────────────────────────────────────────────────────
HEART_RATE_SERVICE    = "0000180d-0000-1000-8000-00805f9b34fb"   # Wahoo TICKR
FITNESS_MACHINE_SERVICE = "00001826-0000-1000-8000-00805f9b34fb" # Wahoo KICKR (FTMS)


async def quick_check(device):
    """Attempt a brief GATT connection and return the device type if it matches.

    Opens a connection with a 3-second timeout.  On success it checks the
    device's service UUIDs against the two profiles above.  Any exception
    (timeout, refused connection, etc.) is silently swallowed and
    ``(None, None)`` is returned.

    Returns:
        (device, "TICKR") if Heart Rate Service found
        (device, "KICKR") if Fitness Machine Service found
        (None, None) if neither service is present or connection failed
    """
    try:
        async with BleakClient(device, timeout=3) as client:
            # Read the GATT service table and normalise UUIDs to lower-case
            service_uuids = [s.uuid.lower() for s in client.services]

            has_hr   = HEART_RATE_SERVICE    in service_uuids
            has_ftms = FITNESS_MACHINE_SERVICE in service_uuids

            if has_hr or has_ftms:
                name = device.name if device.name else "(Unknown)"
                device_type = "TICKR" if has_hr else "KICKR"
                print(f"✓ FOUND {device_type}: {name} - {device.address}")
                return device, device_type
    except Exception:
        # Silently skip: device refused connection, timed out, or vanished
        pass
    return None, None


async def main():
    print("Rapid device scan...")

    # Passive BLE advertisement scan — collects devices broadcasting nearby
    devices = await BleakScanner.discover(timeout=10)
    print(f"Checking {len(devices)} devices...\n")

    # Launch probe coroutines for the first 20 discovered devices *in parallel*.
    # asyncio.gather() schedules them all concurrently, so the total wait is
    # roughly equal to one connection timeout rather than 20 sequential ones.
    tasks = [quick_check(dev) for dev in devices[:20]]   # cap at 20 to avoid overloading BT stack
    results = await asyncio.gather(*tasks)

    # Pick the first matching TICKR and KICKR from the results
    tickr = None
    kickr = None

    for dev, dev_type in results:
        if dev and dev_type == "TICKR":
            tickr = dev
        elif dev and dev_type == "KICKR":
            kickr = dev

    if tickr or kickr:
        # Print the ready-to-use logger command so the user can copy-paste it
        print("\n" + "="*60)
        print("SUCCESS! Run this command:")
        tickr_addr = tickr.address if tickr else "NONE"
        kickr_addr = kickr.address if kickr else "NONE"
        print(f"\npython wahoo_ble_logger.py --tickr-address {tickr_addr} --kickr-address {kickr_addr}\n")
        print("="*60)
    else:
        # Common reasons for no results and how to fix them
        print("\nNo devices found. Try:")
        print("1. Unpair devices from System Settings → Bluetooth")
        print("2. Wear TICKR (needs to detect heartbeat)")
        print("3. Pedal on KICKR")

asyncio.run(main())
