#!/usr/bin/env python3
"""
Helper script to find Wahoo devices by their GATT services.
"""

import asyncio
from bleak import BleakScanner, BleakClient

# Service UUIDs to look for
HEART_RATE_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"
FITNESS_MACHINE_SERVICE = "00001826-0000-1000-8000-00805f9b34fb"
CYCLING_POWER_SERVICE = "00001818-0000-1000-8000-00805f9b34fb"


async def find_devices():
    """Scan for all BLE devices and check their services."""
    print("Scanning for BLE devices with fitness services...")
    print("This may take 15-20 seconds...\n")
    
    devices = await BleakScanner.discover(timeout=15)
    
    tickr_candidates = []
    kickr_candidates = []
    
    for device in devices:
        # Try to connect briefly and check services
        try:
            async with BleakClient(device, timeout=5) as client:
                services = client.services
                service_uuids = [s.uuid.lower() for s in services]
                
                has_hr = HEART_RATE_SERVICE in service_uuids
                has_ftms = FITNESS_MACHINE_SERVICE in service_uuids
                has_power = CYCLING_POWER_SERVICE in service_uuids
                
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
                    
                    print(f"\nAll services:")
                    for service in services:
                        print(f"  - {service.uuid}: {service.description}")
        
        except Exception:
            # Skip devices we can't connect to
            pass
    
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
        print("\n" + "="*60)
        print("RUN THIS COMMAND:")
        tickr_addr = tickr_candidates[0].address if tickr_candidates else "NONE"
        kickr_addr = kickr_candidates[0].address if kickr_candidates else "NONE"
        print(f"\npython wahoo_ble_logger.py --tickr-address {tickr_addr} --kickr-address {kickr_addr}")
        print("="*60)


if __name__ == "__main__":
    asyncio.run(find_devices())
