#!/usr/bin/env python3
"""
Quick discovery - tries to connect to each device and reports which have HR/FTMS services.
"""

import asyncio
from bleak import BleakScanner, BleakClient

HEART_RATE_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"
FITNESS_MACHINE_SERVICE = "00001826-0000-1000-8000-00805f9b34fb"


async def quick_check(device):
    """Quick check if device has HR or FTMS service."""
    try:
        async with BleakClient(device, timeout=3) as client:
            service_uuids = [s.uuid.lower() for s in client.services]
            
            has_hr = HEART_RATE_SERVICE in service_uuids
            has_ftms = FITNESS_MACHINE_SERVICE in service_uuids
            
            if has_hr or has_ftms:
                name = device.name if device.name else "(Unknown)"
                device_type = "TICKR" if has_hr else "KICKR"
                print(f"✓ FOUND {device_type}: {name} - {device.address}")
                return device, device_type
    except:
        pass
    return None, None


async def main():
    print("Rapid device scan...")
    devices = await BleakScanner.discover(timeout=10)
    print(f"Checking {len(devices)} devices...\n")
    
    # Check devices in parallel (faster)
    tasks = [quick_check(dev) for dev in devices[:20]]  # Check first 20
    results = await asyncio.gather(*tasks)
    
    tickr = None
    kickr = None
    
    for dev, dev_type in results:
        if dev and dev_type == "TICKR":
            tickr = dev
        elif dev and dev_type == "KICKR":
            kickr = dev
    
    if tickr or kickr:
        print("\n" + "="*60)
        print("SUCCESS! Run this command:")
        tickr_addr = tickr.address if tickr else "NONE"
        kickr_addr = kickr.address if kickr else "NONE"
        print(f"\npython wahoo_ble_logger.py --tickr-address {tickr_addr} --kickr-address {kickr_addr}\n")
        print("="*60)
    else:
        print("\nNo devices found. Try:")
        print("1. Unpair devices from System Settings → Bluetooth")
        print("2. Wear TICKR (needs to detect heartbeat)")
        print("3. Pedal on KICKR")

asyncio.run(main())
