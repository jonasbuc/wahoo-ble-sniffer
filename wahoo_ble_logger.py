#!/usr/bin/env python3
"""
Wahoo BLE Logger
Logs live BLE data from Wahoo TICKR (heart rate) and KICKR SNAP (trainer) to SQLite.
"""

import asyncio
import argparse
import logging
import sqlite3
import struct
import time
from typing import Optional, Dict, Any, Tuple
from contextlib import closing

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice


# GATT Service and Characteristic UUIDs
HEART_RATE_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
HEART_RATE_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"

FITNESS_MACHINE_SERVICE_UUID = "00001826-0000-1000-8000-00805f9b34fb"
INDOOR_BIKE_DATA_UUID = "00002ad2-0000-1000-8000-00805f9b34fb"

CYCLING_POWER_SERVICE_UUID = "00001818-0000-1000-8000-00805f9b34fb"
CYCLING_POWER_MEASUREMENT_UUID = "00002a63-0000-1000-8000-00805f9b34fb"

# Reconnection settings
RECONNECT_DELAY_SECONDS = 5
SCAN_TIMEOUT_SECONDS = 10

# Database settings
DB_NAME = "training.db"


class SQLiteLogger:
    """Handles SQLite database operations for logging metrics."""
    
    def __init__(self, db_name: str = DB_NAME):
        self.db_name = db_name
        self._init_database()
    
    def _init_database(self) -> None:
        """Initialize the database with WAL mode and create tables."""
        with closing(sqlite3.connect(self.db_name)) as conn:
            # Enable WAL mode for better concurrency
            conn.execute("PRAGMA journal_mode=WAL")
            
            # Create metrics table if it doesn't exist
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metrics (
                    ts REAL NOT NULL,
                    hr_bpm INTEGER,
                    rr_ms INTEGER,
                    power_w INTEGER,
                    cadence_rpm REAL,
                    speed_kph REAL
                )
            """)
            
            # Create index on timestamp for efficient queries
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(ts)
            """)
            
            conn.commit()
            logging.info(f"Database initialized: {self.db_name}")
    
    def log_metric(
        self,
        hr_bpm: Optional[int] = None,
        rr_ms: Optional[int] = None,
        power_w: Optional[int] = None,
        cadence_rpm: Optional[float] = None,
        speed_kph: Optional[float] = None
    ) -> None:
        """Log a metric to the database."""
        timestamp = time.time()
        
        with closing(sqlite3.connect(self.db_name)) as conn:
            conn.execute(
                """
                INSERT INTO metrics (ts, hr_bpm, rr_ms, power_w, cadence_rpm, speed_kph)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (timestamp, hr_bpm, rr_ms, power_w, cadence_rpm, speed_kph)
            )
            conn.commit()


class HeartRateParser:
    """Parses Heart Rate Measurement characteristic data."""
    
    @staticmethod
    def parse(data: bytearray) -> Dict[str, Any]:
        """
        Parse Heart Rate Measurement data.
        
        Returns dict with 'bpm' and optionally 'rr_intervals_ms'.
        """
        if len(data) < 2:
            logging.warning(f"HR data too short: {len(data)} bytes")
            return {}
        
        flags = data[0]
        hr_format = flags & 0x01  # 0 = uint8, 1 = uint16
        
        # Parse heart rate value
        if hr_format == 0:
            bpm = data[1]
            offset = 2
        else:
            bpm = struct.unpack_from("<H", data, 1)[0]
            offset = 3
        
        result = {"bpm": bpm}
        
        # Check for RR-Interval presence (bit 4)
        has_rr = (flags & 0x10) != 0
        
        if has_rr and len(data) >= offset + 2:
            # RR-Intervals are in 1/1024 second resolution
            rr_intervals = []
            while offset + 1 < len(data):
                rr_1024 = struct.unpack_from("<H", data, offset)[0]
                rr_ms = int((rr_1024 / 1024.0) * 1000)
                rr_intervals.append(rr_ms)
                offset += 2
            
            if rr_intervals:
                result["rr_intervals_ms"] = rr_intervals
        
        return result


class CyclingPowerParser:
    """Parses Cycling Power Measurement characteristic."""
    
    @staticmethod
    def parse(data: bytearray, debug: bool = False) -> Dict[str, Any]:
        """
        Parse Cycling Power Measurement data.
        
        Returns dict with 'power_w' and optionally 'cadence_rpm'.
        """
        if len(data) < 4:
            logging.warning(f"Cycling Power data too short: {len(data)} bytes")
            return {}
        
        try:
            # First 2 bytes are flags (little-endian)
            flags = struct.unpack_from("<H", data, 0)[0]
            offset = 2
            
            if debug:
                logging.debug(f"Cycling Power flags: 0x{flags:04x}, raw: {data.hex()}")
            
            result = {}
            
            # Instantaneous Power (sint16, always present)
            if offset + 1 < len(data):
                power = struct.unpack_from("<h", data, offset)[0]
                result["power_w"] = power
                offset += 2
            
            # Bit 0: Pedal Power Balance Present
            if (flags & 0x01) and offset < len(data):
                offset += 1  # Skip pedal power balance
            
            # Bit 1: Pedal Power Balance Reference (just a flag, no data)
            
            # Bit 2: Accumulated Torque Present
            if (flags & 0x04) and offset + 1 < len(data):
                offset += 2  # Skip accumulated torque
            
            # Bit 3: Accumulated Torque Source (just a flag, no data)
            
            # Bit 4: Wheel Revolution Data Present
            if (flags & 0x10) and offset + 5 < len(data):
                offset += 6  # Skip cumulative wheel revolutions (uint32) + last wheel event time (uint16)
            
            # Bit 5: Crank Revolution Data Present
            if (flags & 0x20) and offset + 3 < len(data):
                cumulative_crank_revs = struct.unpack_from("<H", data, offset)[0]
                offset += 2
                last_crank_event_time = struct.unpack_from("<H", data, offset)[0]
                offset += 2
                
                # Calculate cadence from crank revolution data (requires tracking previous values)
                # For now, we'll just note it's present
                # To calculate cadence, we'd need: RPM = (delta_revs / delta_time) * 60 * 1024
                # where delta_time is in 1/1024 second units
                
            # Bit 6: Extreme Force Magnitudes Present
            if (flags & 0x40) and offset + 3 < len(data):
                offset += 4  # Skip max and min force magnitudes
            
            # Bit 7: Extreme Torque Magnitudes Present
            if (flags & 0x80) and offset + 3 < len(data):
                offset += 4  # Skip max and min torque magnitudes
            
            # Bits 8-11: Extreme Angles Present
            if (flags & 0x0F00) and offset + 2 < len(data):
                # Skip extreme angles (3 uint12 fields packed into 4.5 bytes)
                offset += 3
            
            # Bit 12: Top Dead Spot Angle Present
            if (flags & 0x1000) and offset + 1 < len(data):
                offset += 2
            
            # Bit 13: Bottom Dead Spot Angle Present
            if (flags & 0x2000) and offset + 1 < len(data):
                offset += 2
            
            # Bit 14: Accumulated Energy Present
            if (flags & 0x4000) and offset + 1 < len(data):
                offset += 2
            
            # Bit 15: Offset Compensation Indicator (just a flag, no data)
            
            return result
            
        except struct.error as e:
            logging.warning(f"Cycling Power parsing error: {e}, data: {data.hex()}")
            return {}


class FTMSIndoorBikeParser:
    """Parses FTMS Indoor Bike Data characteristic."""
    
    @staticmethod
    def parse(data: bytearray, debug: bool = False) -> Dict[str, Any]:
        """
        Parse FTMS Indoor Bike Data.
        
        Returns dict with available fields: 'speed_kph', 'cadence_rpm', 'power_w'.
        """
        if len(data) < 2:
            logging.warning(f"FTMS data too short: {len(data)} bytes")
            return {}
        
        try:
            # First 2 bytes are flags (little-endian)
            flags = struct.unpack_from("<H", data, 0)[0]
            offset = 2
            
            if debug:
                logging.debug(f"FTMS flags: 0x{flags:04x}, raw: {data.hex()}")
            
            result = {}
            
            # Bit 0: More Data (ignore, just indicates more fields present)
            # Bit 1: Average Speed Present
            has_avg_speed = (flags & 0x02) != 0
            
            # Bit 2: Instantaneous Cadence Present
            has_cadence = (flags & 0x04) != 0
            
            # Bit 3: Average Cadence Present
            has_avg_cadence = (flags & 0x08) != 0
            
            # Bit 4: Total Distance Present
            has_distance = (flags & 0x10) != 0
            
            # Bit 5: Resistance Level Present
            has_resistance = (flags & 0x20) != 0
            
            # Bit 6: Instantaneous Power Present
            has_power = (flags & 0x40) != 0
            
            # Bit 7: Average Power Present
            has_avg_power = (flags & 0x80) != 0
            
            # Bit 8: Expended Energy Present
            has_energy = (flags & 0x100) != 0
            
            # Bit 9: Heart Rate Present
            has_hr = (flags & 0x200) != 0
            
            # Bit 10: Metabolic Equivalent Present
            has_met = (flags & 0x400) != 0
            
            # Bit 11: Elapsed Time Present
            has_elapsed = (flags & 0x800) != 0
            
            # Bit 12: Remaining Time Present
            has_remaining = (flags & 0x1000) != 0
            
            # Parse fields in order based on spec
            # Instantaneous Speed (always present per spec, uint16, 0.01 km/h)
            if offset + 1 < len(data):
                speed_raw = struct.unpack_from("<H", data, offset)[0]
                result["speed_kph"] = speed_raw * 0.01
                offset += 2
            
            # Average Speed (uint16, 0.01 km/h)
            if has_avg_speed and offset + 1 < len(data):
                offset += 2  # Skip average speed
            
            # Instantaneous Cadence (uint16, 0.5 rpm)
            if has_cadence and offset + 1 < len(data):
                cadence_raw = struct.unpack_from("<H", data, offset)[0]
                result["cadence_rpm"] = cadence_raw * 0.5
                offset += 2
            
            # Average Cadence (uint16, 0.5 rpm)
            if has_avg_cadence and offset + 1 < len(data):
                offset += 2  # Skip average cadence
            
            # Total Distance (uint24, meters)
            if has_distance and offset + 2 < len(data):
                offset += 3  # Skip distance
            
            # Resistance Level (sint16)
            if has_resistance and offset + 1 < len(data):
                offset += 2  # Skip resistance
            
            # Instantaneous Power (sint16, watts)
            if has_power and offset + 1 < len(data):
                power_raw = struct.unpack_from("<h", data, offset)[0]  # signed
                result["power_w"] = power_raw
                offset += 2
            
            # Average Power (sint16, watts)
            if has_avg_power and offset + 1 < len(data):
                offset += 2  # Skip average power
            
            # Expended Energy (3 fields: Total Energy uint16, Energy per Hour uint16, Energy per Minute uint8)
            if has_energy and offset + 4 < len(data):
                offset += 5  # Skip energy fields
            
            # Heart Rate (uint8, bpm)
            if has_hr and offset < len(data):
                offset += 1  # Skip HR (we get this from TICKR)
            
            # Metabolic Equivalent (uint8, 0.1 MET)
            if has_met and offset < len(data):
                offset += 1  # Skip MET
            
            # Elapsed Time (uint16, seconds)
            if has_elapsed and offset + 1 < len(data):
                offset += 2  # Skip elapsed time
            
            # Remaining Time (uint16, seconds)
            if has_remaining and offset + 1 < len(data):
                offset += 2  # Skip remaining time
            
            return result
            
        except struct.error as e:
            logging.warning(f"FTMS parsing error: {e}, data: {data.hex()}")
            return {}


class WahooDevice:
    """Represents a connected Wahoo BLE device."""
    
    def __init__(
        self,
        device: BLEDevice,
        characteristic_uuid: str,
        parser,
        db_logger: SQLiteLogger,
        debug: bool = False
    ):
        self.device = device
        self.characteristic_uuid = characteristic_uuid
        self.parser = parser
        self.db_logger = db_logger
        self.debug = debug
        self.client: Optional[BleakClient] = None
        self.running = False
    
    async def notification_handler(self, sender: int, data: bytearray) -> None:
        """Handle incoming BLE notifications."""
        try:
            parsed = self.parser.parse(data, debug=self.debug) if hasattr(self.parser.parse, '__code__') and 'debug' in self.parser.parse.__code__.co_varnames else self.parser.parse(data)
            
            if not parsed:
                return
            
            # Log to database based on device type
            if "bpm" in parsed:
                # Heart rate data
                hr_bpm = parsed["bpm"]
                rr_ms = parsed.get("rr_intervals_ms", [None])[0] if parsed.get("rr_intervals_ms") else None
                
                self.db_logger.log_metric(hr_bpm=hr_bpm, rr_ms=rr_ms)
                
                log_msg = f"[{self.device.name}] HR: {hr_bpm} bpm"
                if rr_ms:
                    log_msg += f", RR: {rr_ms} ms"
                logging.info(log_msg)
            
            elif any(k in parsed for k in ["speed_kph", "cadence_rpm", "power_w"]):
                # Trainer data
                speed = parsed.get("speed_kph")
                cadence = parsed.get("cadence_rpm")
                power = parsed.get("power_w")
                
                self.db_logger.log_metric(
                    power_w=power,
                    cadence_rpm=cadence,
                    speed_kph=speed
                )
                
                parts = []
                if power is not None:
                    parts.append(f"Power: {power} W")
                if cadence is not None:
                    parts.append(f"Cadence: {cadence:.1f} rpm")
                if speed is not None:
                    parts.append(f"Speed: {speed:.1f} km/h")
                
                if parts:
                    logging.info(f"[{self.device.name}] {', '.join(parts)}")
        
        except Exception as e:
            logging.error(f"Error handling notification from {self.device.name}: {e}")
    
    async def connect_and_subscribe(self) -> bool:
        """Connect to device and subscribe to notifications."""
        try:
            logging.info(f"Connecting to {self.device.name} ({self.device.address})...")
            self.client = BleakClient(self.device)
            await self.client.connect()
            
            if not self.client.is_connected:
                logging.error(f"Failed to connect to {self.device.name}")
                return False
            
            logging.info(f"Connected to {self.device.name}")
            
            # List all services and characteristics for debugging
            if self.debug or "KICKR" in self.device.name:
                logging.info(f"Available services and characteristics for {self.device.name}:")
                for service in self.client.services:
                    logging.info(f"  Service: {service.uuid} - {service.description}")
                    for char in service.characteristics:
                        logging.info(f"    Characteristic: {char.uuid} - {char.description} (Properties: {char.properties})")
            
            # Subscribe to notifications
            await self.client.start_notify(
                self.characteristic_uuid,
                self.notification_handler
            )
            
            logging.info(f"Subscribed to notifications from {self.device.name}")
            return True
            
        except Exception as e:
            logging.error(f"Error connecting to {self.device.name}: {e}")
            return False
    
    async def disconnect(self) -> None:
        """Disconnect from device."""
        if self.client and self.client.is_connected:
            try:
                await self.client.disconnect()
                logging.info(f"Disconnected from {self.device.name}")
            except Exception as e:
                logging.error(f"Error disconnecting from {self.device.name}: {e}")
    
    async def run(self) -> None:
        """Main loop with auto-reconnect."""
        self.running = True
        
        while self.running:
            try:
                connected = await self.connect_and_subscribe()
                
                if not connected:
                    logging.warning(f"Will retry {self.device.name} in {RECONNECT_DELAY_SECONDS}s...")
                    await asyncio.sleep(RECONNECT_DELAY_SECONDS)
                    continue
                
                # Stay connected and process notifications
                while self.running and self.client and self.client.is_connected:
                    await asyncio.sleep(1)
                
                # If we get here, device disconnected
                if self.running:
                    logging.warning(f"{self.device.name} disconnected. Reconnecting in {RECONNECT_DELAY_SECONDS}s...")
                    await asyncio.sleep(RECONNECT_DELAY_SECONDS)
            
            except Exception as e:
                logging.error(f"Error in {self.device.name} main loop: {e}")
                if self.running:
                    await asyncio.sleep(RECONNECT_DELAY_SECONDS)
    
    async def stop(self) -> None:
        """Stop the device loop."""
        self.running = False
        await self.disconnect()


async def scan_for_device(device_name_contains: str, timeout: int = SCAN_TIMEOUT_SECONDS, show_all: bool = False) -> Optional[BLEDevice]:
    """Scan for a BLE device by name substring."""
    logging.info(f"Scanning for device containing '{device_name_contains}'...")
    
    try:
        devices = await BleakScanner.discover(timeout=timeout)
        
        if show_all:
            logging.info(f"Found {len(devices)} BLE devices:")
            for device in devices:
                name = device.name if device.name else "(Unknown)"
                logging.info(f"  - {name} ({device.address})")
        
        for device in devices:
            if device.name and device_name_contains.upper() in device.name.upper():
                logging.info(f"Found {device.name} at {device.address}")
                return device
        
        logging.warning(f"No device found containing '{device_name_contains}'")
        return None
        
    except Exception as e:
        logging.error(f"Error scanning for devices: {e}")
        return None


async def main_async(
    tickr_address: Optional[str] = None,
    kickr_address: Optional[str] = None,
    debug: bool = False,
    show_all_devices: bool = False
) -> None:
    """Main async entry point."""
    
    # Initialize database logger
    db_logger = SQLiteLogger()
    
    # Find or use specified devices
    tickr_device = None
    kickr_device = None
    
    if tickr_address:
        logging.info(f"Using specified TICKR address: {tickr_address}")
        # Create a dummy BLEDevice with the address
        # We'll need to scan to get the full device object
        devices = await BleakScanner.discover(timeout=SCAN_TIMEOUT_SECONDS)
        for device in devices:
            if device.address.upper() == tickr_address.upper():
                tickr_device = device
                break
        if not tickr_device:
            logging.error(f"Could not find TICKR at address {tickr_address}")
    else:
        tickr_device = await scan_for_device("TICKR", show_all=show_all_devices)
    
    if kickr_address:
        logging.info(f"Using specified KICKR address: {kickr_address}")
        devices = await BleakScanner.discover(timeout=SCAN_TIMEOUT_SECONDS)
        for device in devices:
            if device.address.upper() == kickr_address.upper():
                kickr_device = device
                break
        if not kickr_device:
            logging.error(f"Could not find KICKR at address {kickr_address}")
    else:
        kickr_device = await scan_for_device("KICKR", show_all=show_all_devices)
    
    if not tickr_device and not kickr_device:
        logging.error("No Wahoo devices found. Exiting.")
        return
    
    # Create device handlers
    tasks = []
    devices_list = []
    
    if tickr_device:
        tickr = WahooDevice(
            device=tickr_device,
            characteristic_uuid=HEART_RATE_MEASUREMENT_UUID,
            parser=HeartRateParser,
            db_logger=db_logger,
            debug=debug
        )
        devices_list.append(tickr)
        tasks.append(asyncio.create_task(tickr.run()))
    
    if kickr_device:
        # Try to determine which characteristic to use
        kickr_char_uuid = INDOOR_BIKE_DATA_UUID
        kickr_parser = FTMSIndoorBikeParser
        
        # Quick check to see if device has Cycling Power instead of FTMS
        try:
            async with BleakClient(kickr_device, timeout=5) as temp_client:
                service_uuids = [s.uuid.lower() for s in temp_client.services]
                has_ftms = FITNESS_MACHINE_SERVICE_UUID in service_uuids
                has_cycling_power = CYCLING_POWER_SERVICE_UUID in service_uuids
                
                if not has_ftms and has_cycling_power:
                    logging.info(f"KICKR using Cycling Power Service instead of FTMS")
                    kickr_char_uuid = CYCLING_POWER_MEASUREMENT_UUID
                    kickr_parser = CyclingPowerParser
        except Exception as e:
            logging.debug(f"Could not pre-check KICKR services: {e}")
        
        kickr = WahooDevice(
            device=kickr_device,
            characteristic_uuid=kickr_char_uuid,
            parser=kickr_parser,
            db_logger=db_logger,
            debug=debug
        )
        devices_list.append(kickr)
        tasks.append(asyncio.create_task(kickr.run()))
    
    logging.info("Starting data collection. Press Ctrl+C to stop.")
    
    try:
        # Run all device tasks concurrently
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logging.info("Shutting down...")
    finally:
        # Clean shutdown
        for device in devices_list:
            await device.stop()


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Log live BLE data from Wahoo TICKR and KICKR devices to SQLite"
    )
    parser.add_argument(
        "--tickr-address",
        type=str,
        help="MAC address of TICKR device (optional, auto-scans if not provided)"
    )
    parser.add_argument(
        "--kickr-address",
        type=str,
        help="MAC address of KICKR device (optional, auto-scans if not provided)"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging with raw BLE data"
    )
    parser.add_argument(
        "--show-all-devices",
        action="store_true",
        help="Show all discovered BLE devices during scan"
    )
    
    args = parser.parse_args()
    
    # Configure logging
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # Run async main
    try:
        asyncio.run(main_async(
            tickr_address=args.tickr_address,
            kickr_address=args.kickr_address,
            debug=args.debug,
            show_all_devices=args.show_all_devices
        ))
    except KeyboardInterrupt:
        logging.info("Interrupted by user")


if __name__ == "__main__":
    main()
