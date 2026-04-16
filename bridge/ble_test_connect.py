#!/usr/bin/env python3
"""
Test script to connect to a BLE device by address and subscribe to Heart Rate notifications.
Usage:
  ./ble_test_connect.py <BLE_ADDRESS>

It prints discovered devices (address + name) if no address given, or attempts a connect+subscribe
if an address is supplied.
"""
import argparse
import asyncio
import logging
from typing import Optional

try:
    from bleak import BleakClient, BleakScanner
    from bleak.exc import BleakDeviceNotFoundError
except Exception:
    print("Please install bleak in your venv: pip install bleak")
    raise

HR_UUID = "00002a37-0000-1000-8000-00805f9b34fb"


async def list_devices(scan_time: float = 5.0):
    print(f"Scanning {scan_time:.0f}s...")
    devices = await BleakScanner.discover(timeout=scan_time)
    if not devices:
        print("No BLE devices found during scan")
        return
    for d in devices:
        # On macOS bleak returns UUID-like addresses
        print(d.address, d.name)


async def connect_and_subscribe(address: str, timeout: Optional[float] = None):
    print(f"Attempting connection to {address}...")

    def hr_handler(sender, data: bytes):
        try:
            flags = data[0]
            hr_format = flags & 0x01
            if hr_format == 0:
                hr = data[1]
            else:
                hr = int.from_bytes(data[1:3], "little")
            print(f"HR update: {hr}")
        except Exception as e:
            print("Failed to parse HR notification:", e)

    async def _run():
        try:
            async with BleakClient(address) as client:
                print("Connected, fetching services...")
                # Bleak's API has varied across versions: some provide an
                # async get_services() coroutine, others expose a populated
                # `services` property. Be defensive and handle both so this
                # helper works with multiple bleak releases.
                services = None
                get_services = getattr(client, "get_services", None)
                if callable(get_services):
                    try:
                        # prefer awaiting if it's a coroutine function
                        services = await get_services()
                        print("Fetched services via client.get_services()")
                    except TypeError:
                        # get_services might be a regular function (rare) — call it
                        services = get_services()
                        print("Fetched services via client.get_services() (sync)")
                elif hasattr(client, "services"):
                    # fallback: Bleak often populates a `services` attribute
                    services = client.services
                    print("Using client.services property")
                else:
                    services = []
                    print("No service API available on Bleak client; continuing with empty list")
                print("Discovered services and characteristics:")
                hr_found = False
                for svc in services:
                    try:
                        print(f" Service {getattr(svc, 'uuid', svc)}")
                        for ch in getattr(svc, 'characteristics', []):
                            cu = getattr(ch, 'uuid', str(ch))
                            props = getattr(ch, 'properties', None)
                            print(f"  - Char {cu} props={props}")
                            if HR_UUID in cu.lower():
                                hr_found = True
                    except Exception:
                        pass

                print("HR characteristic present:", hr_found)
                if not hr_found:
                    print("No HR characteristic exposed by this device. Exiting.")
                    return

                try:
                    await client.start_notify(HR_UUID, hr_handler)
                    print("Subscribed to HR notifications. Waiting for updates (ctrl-c to stop)...")
                    while True:
                        await asyncio.sleep(1.0)
                finally:
                    try:
                        await client.stop_notify(HR_UUID)
                    except Exception:
                        pass

        except BleakDeviceNotFoundError:
            # Let callers know the device address wasn't found so they can
            # optionally attempt a name-based discovery and retry.
            raise
        except Exception as e:
            print("Connection failed:", repr(e))
            # Propagate so higher-level retry wrappers can decide to reconnect
            raise

    if timeout:
        try:
            await asyncio.wait_for(_run(), timeout=timeout)
        except asyncio.TimeoutError:
            print(f"Connection attempt timed out after {timeout}s")
        except BleakDeviceNotFoundError:
            # propagate to caller for optional name-based retry
            raise
    else:
        try:
            await _run()
        except BleakDeviceNotFoundError:
            # propagate to caller for optional name-based retry
            raise


async def connect_with_retry(
    address: str,
    timeout: Optional[float] = None,
    attempts: int = 2,
    base_backoff: float = 1.0,
):
    """Attempt to connect up to `attempts` times with exponential backoff.

    If BleakDeviceNotFoundError is raised it is propagated immediately (not retried).
    Other exceptions cause a retry up to `attempts`.
    """
    last_exc = None
    for attempt in range(1, attempts + 1):
        print(f"Connection attempt {attempt}/{attempts} to {address}")
        try:
            await connect_and_subscribe(address, timeout=timeout)
            return
        except BleakDeviceNotFoundError:
            # Address not present — don't retry here, let caller handle name fallback
            raise
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"Attempt {attempt} failed: {e}")
            last_exc = e
            if attempt < attempts:
                backoff = base_backoff * (2 ** (attempt - 1))
                print(f"Waiting {backoff:.1f}s before retry...")
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    raise
    print("All connection attempts failed.")
    if last_exc:
        raise last_exc


async def find_address_by_name(name: str, scan_time: float = 10.0):
    """Scan for 'scan_time' seconds and return (address, device_name) of the
    first discovered device whose .name contains the given name (case-insensitive).
    Returns (None, None) when not found."""
    print(f"Scanning {scan_time:.0f}s for a device with name containing '{name}'...")
    devices = await BleakScanner.discover(timeout=scan_time)
    for d in devices:
        try:
            if d.name and name.lower() in d.name.lower():
                return d.address, d.name
        except Exception:
            continue
    return None, None


def parse_args():
    p = argparse.ArgumentParser(description="BLE test connect and subscribe to HR notifications")
    p.add_argument("--address", "-a", help="BLE device address/identifier to connect to")
    p.add_argument("--scan-time", type=float, default=5.0, help="Scan duration in seconds when listing devices")
    p.add_argument("--timeout", type=float, default=None, help="Connection attempt timeout in seconds")
    p.add_argument("--debug", action="store_true", help="Enable debug logging for Bleak/Python")
    p.add_argument("--name", "-n", help="Device name (or a substring) to search for if address lookup fails")
    return p.parse_args()


def main():
    args = parse_args()
    if args.debug:
        # Enable verbose logging to help diagnose connection/discovery issues
        logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s:%(name)s: %(message)s")
        # make sure bleak logs are visible
        logging.getLogger("bleak").setLevel(logging.DEBUG)

    if not args.address:
        asyncio.run(list_devices(scan_time=args.scan_time))
    else:
        try:
            asyncio.run(connect_with_retry(args.address, timeout=args.timeout))
        except BleakDeviceNotFoundError:
            # Address wasn't found by Bleak/CoreBluetooth. If the user supplied
            # a name, try scanning for a device with that name and retry once.
            if args.name:
                addr, found_name = asyncio.run(find_address_by_name(args.name, scan_time=max(10.0, args.scan_time)))
                if addr:
                    print(f"Found device by name: {addr} -> {found_name}. Retrying connection...")
                    try:
                        asyncio.run(connect_with_retry(addr, timeout=args.timeout))
                    except Exception as e:
                        print("Retry after name-based discovery failed:", e)
                else:
                    print(
                        f"Device with address {args.address} was not found and "
                        f"no device matching name '{args.name}' was discovered."
                    )
            else:
                print(f"Device with address {args.address} was not found")


if __name__ == "__main__":
    main()
