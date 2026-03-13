#!/usr/bin/env python3
"""
Wahoo BLE Logger
================
Logs live BLE data from Wahoo TICKR (heart rate) and KICKR SNAP (trainer) to SQLite.

Architecture overview:
  - BleakScanner discovers nearby BLE devices by name
  - Each device gets a WahooDevice instance that owns its own reconnect loop
  - Incoming GATT notifications are parsed by a static Parser class (HeartRateParser /
    CyclingPowerParser / FTMSIndoorBikeParser) and written to SQLite via SQLiteLogger
  - asyncio.gather() keeps both device loops running concurrently on a single thread

Supported profiles:
  - Heart Rate Service  (0x180D / char 0x2A37) — TICKR
  - Fitness Machine     (0x1826 / char 0x2AD2) — KICKR SNAP via FTMS Indoor Bike Data
  - Cycling Power       (0x1818 / char 0x2A63) — KICKR older firmware fallback

Usage:
  python wahoo_ble_logger.py [--debug] [--show-all-devices]
  python wahoo_ble_logger.py --tickr-address AA:BB:CC:DD:EE:FF
"""

import argparse
import asyncio
import logging
import sqlite3
import struct
import threading
import time
from typing import Any, Dict, Optional, Callable, Awaitable, cast, Type

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

# ── GATT UUIDs ───────────────────────────────────────────────────────────────
# Standard Bluetooth GATT UUIDs in full 128-bit form.
# Short-form IDs (e.g. 0x180D) expand to 0000xxxx-0000-1000-8000-00805f9b34fb.

# Heart Rate service (TICKR)
HEART_RATE_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
HEART_RATE_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"

# Fitness Machine Service (FTMS) — used by KICKR SNAP for speed/cadence/power
FITNESS_MACHINE_SERVICE_UUID = "00001826-0000-1000-8000-00805f9b34fb"
INDOOR_BIKE_DATA_UUID = "00002ad2-0000-1000-8000-00805f9b34fb"

# Cycling Power service — older KICKR firmware that doesn't expose FTMS
CYCLING_POWER_SERVICE_UUID = "00001818-0000-1000-8000-00805f9b34fb"
CYCLING_POWER_MEASUREMENT_UUID = "00002a63-0000-1000-8000-00805f9b34fb"

# ── Reconnection / scan settings ─────────────────────────────────────────────
# How long to wait before retrying a dropped connection (seconds)
RECONNECT_DELAY_SECONDS = 5
# BLE passive scan duration before giving up (seconds)
SCAN_TIMEOUT_SECONDS = 10

# ── Database settings ─────────────────────────────────────────────────────────
DB_NAME = "training.db"
# After a database error, wait this long before attempting to re-open the file
DB_RETRY_DELAY_SECONDS = 5.0


class SQLiteLogger:
    """Thread-safe SQLite logger for cycling metrics.

    Opens (or creates) a WAL-mode SQLite database and exposes a single
    ``log_metric()`` method that can be called from any thread.  If the
    database file becomes unavailable the logger will silently drop rows
    and automatically retry re-opening it after ``DB_RETRY_DELAY_SECONDS``.
    """

    def __init__(self, db_name: str = DB_NAME):
        self.db_name = db_name
        # threading.Lock serialises all DB access so the BLE callbacks
        # (which run on the asyncio thread) and any future threads are safe.
        self._lock = threading.Lock()
        # Epoch time after which we are allowed to attempt a reconnect.
        # Zero means "try immediately".
        self._next_retry_after = 0.0
        try:
            # check_same_thread=False is safe because we use _lock ourselves
            self._conn: Optional[sqlite3.Connection] = sqlite3.connect(
                self.db_name, check_same_thread=False
            )
        except sqlite3.Error as exc:
            logging.error("Failed to open SQLite database %s: %s", self.db_name, exc)
            self._conn = None
            self._next_retry_after = time.time() + DB_RETRY_DELAY_SECONDS
        else:
            with self._lock:
                self._init_database()

    def _init_database(self) -> None:
        """Create schema and enable WAL mode (called once at startup or after reconnect)."""
        if not self._conn:
            return

        # WAL (Write-Ahead Logging) allows readers to see committed data while
        # a write is in progress — important when multiple processes share the DB.
        self._conn.execute("PRAGMA journal_mode=WAL")

        # All columns are nullable so partial rows are OK (e.g. HR-only or power-only)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS metrics (
                ts REAL NOT NULL,
                hr_bpm INTEGER,
                rr_ms INTEGER,
                power_w INTEGER,
                cadence_rpm REAL,
                speed_kph REAL
            )
            """
        )

        # Index on timestamp enables fast time-range queries and ordered reads
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(ts)
            """
        )

        self._conn.commit()
        logging.info("Database initialized: %s", self.db_name)

    def log_metric(
        self,
        hr_bpm: Optional[int] = None,
        rr_ms: Optional[int] = None,
        power_w: Optional[int] = None,
        cadence_rpm: Optional[float] = None,
        speed_kph: Optional[float] = None,
    ) -> None:
        """Insert one row into the metrics table.

        All data columns are optional — pass only the fields available for
        the current notification type.  On any database error the row is
        discarded and a reconnect is scheduled.
        """
        timestamp = time.time()

        with self._lock:
            if not self._conn:
                # Database is unavailable; honour the backoff before retrying
                if timestamp < self._next_retry_after:
                    logging.debug(
                        "Skipping SQLite reconnect attempt until %.3f (now %.3f)",
                        self._next_retry_after,
                        timestamp,
                    )
                    return
                # Backoff elapsed — try to re-open the database
                try:
                    self._conn = sqlite3.connect(self.db_name, check_same_thread=False)
                    logging.debug(
                        "Successfully reconnected to SQLite database %s", self.db_name
                    )
                    self._init_database()
                    self._next_retry_after = 0.0
                except sqlite3.Error as exc:
                    logging.error(
                        "Failed to reopen SQLite database %s: %s", self.db_name, exc
                    )
                    self._conn = None
                    self._next_retry_after = timestamp + DB_RETRY_DELAY_SECONDS
                    return

            try:
                self._conn.execute(
                    """
                    INSERT INTO metrics (ts, hr_bpm, rr_ms, power_w, cadence_rpm, speed_kph)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (timestamp, hr_bpm, rr_ms, power_w, cadence_rpm, speed_kph),
                )
                self._conn.commit()
            except sqlite3.Error as exc:
                # Log the error, roll back any pending transaction, close the
                # broken connection, and schedule a reconnect.
                logging.error("Failed to write metric to %s: %s", self.db_name, exc)
                try:
                    self._conn.rollback()
                    self._conn.close()
                except sqlite3.Error:
                    pass
                finally:
                    self._conn = None
                self._next_retry_after = timestamp + DB_RETRY_DELAY_SECONDS

    def close(self) -> None:
        """Close the SQLite connection. Use explicitly or via a context manager."""
        with self._lock:
            if self._conn:
                try:
                    self._conn.commit()
                    self._conn.close()
                except sqlite3.Error as exc:
                    logging.warning(
                        "Error closing SQLite connection %s: %s", self.db_name, exc
                    )
                finally:
                    self._conn = None

    def __enter__(self) -> "SQLiteLogger":
        return self

    def __exit__(
        self,
        exc_type: Optional[type],
        exc_val: Optional[BaseException],
        exc_tb: Optional[Any],
    ) -> None:
        self.close()


class HeartRateParser:
    """Parses the Heart Rate Measurement GATT characteristic (0x2A37).

    Byte layout (Bluetooth Assigned Numbers §3.106):
      Byte 0     — flags
        bit 0: HR value format  0 = uint8, 1 = uint16
        bit 1: Sensor Contact Status (informational, ignored)
        bit 2: Sensor Contact Feature (informational, ignored)
        bit 3: Energy Expended Status (ignored here)
        bit 4: RR-Interval present
      Byte 1[–2] — Heart Rate Value (uint8 or uint16 LE depending on flag bit 0)
      Remaining  — RR-Interval values (uint16 LE, units of 1/1024 s each)
    """

    @staticmethod
    def parse(data: bytearray) -> Dict[str, Any]:
        """Parse a raw Heart Rate Measurement notification.

        Returns a dict with:
          - ``bpm``            : int heart-rate in beats per minute
          - ``rr_intervals_ms``: list[int] RR-intervals in milliseconds (if present)
        """
        if len(data) < 2:
            logging.warning(f"HR data too short: {len(data)} bytes")
            return {}

        flags = data[0]
        # Bit 0 of flags: 0 means HR is packed as a single uint8; 1 means uint16 LE
        hr_format = flags & 0x01

        # Parse heart rate value
        if hr_format == 0:
            # uint8 format — HR fits in one byte (typical range 0-255 bpm)
            bpm = data[1]
            offset = 2          # RR data starts at byte 2
        else:
            # uint16 LE format — used when HR exceeds 255 bpm (rare, but spec-compliant)
            bpm = struct.unpack_from("<H", data, 1)[0]
            offset = 3          # RR data starts at byte 3 (HR occupied bytes 1-2)

        result: Dict[str, Any] = {"bpm": bpm}

        # Bit 4 of flags indicates RR-Interval data is appended after the HR value
        has_rr = (flags & 0x10) != 0

        if has_rr and len(data) >= offset + 2:
            # Each RR-Interval is a uint16 in units of 1/1024 second.
            # Convert to milliseconds: ms = (raw / 1024) * 1000
            rr_intervals = []
            while offset + 1 < len(data):
                rr_1024 = struct.unpack_from("<H", data, offset)[0]
                rr_ms = int((rr_1024 / 1024.0) * 1000)
                rr_intervals.append(rr_ms)
                offset += 2     # Each RR value is 2 bytes

            if rr_intervals:
                result["rr_intervals_ms"] = rr_intervals

        return result


class CyclingPowerParser:
    """Parses the Cycling Power Measurement GATT characteristic (0x2A63).

    This is the older KICKR profile. The notification starts with a 16-bit
    flags field that describes which optional fields follow. Only the fields
    we actually use (power, and optionally cadence) are extracted; the rest
    are skipped by advancing the offset.

    Byte layout (Bluetooth Assigned Numbers §3.68):
      [0-1]  flags   (uint16 LE) — bitmask selecting which fields are present
      [2-3]  Instantaneous Power (sint16 LE, watts) — ALWAYS present
      …      optional fields follow in spec order
    """

    @staticmethod
    def parse(data: bytearray, debug: bool = False) -> Dict[str, Any]:
        """Parse a Cycling Power Measurement notification.

        Returns a dict with:
          - ``power_w``: int instantaneous power in watts
        """
        if len(data) < 4:
            logging.warning(f"Cycling Power data too short: {len(data)} bytes")
            return {}

        try:
            # First 2 bytes are the flags field (little-endian uint16)
            flags = struct.unpack_from("<H", data, 0)[0]
            offset = 2

            if debug:
                logging.debug(f"Cycling Power flags: 0x{flags:04x}, raw: {data.hex()}")

            result = {}

            # Instantaneous Power is always present (sint16 LE), regardless of flags
            if offset + 1 < len(data):
                power = struct.unpack_from("<h", data, offset)[0]   # signed: can be negative
                result["power_w"] = power
                offset += 2

            # The following blocks advance `offset` past each optional field so that
            # later fields (cadence) are read from the correct position.

            # Bit 0: Pedal Power Balance present (uint8, 0.5 % units)
            if (flags & 0x01) and offset < len(data):
                offset += 1

            # Bit 1: Pedal Power Balance Reference — informational flag only, no extra bytes

            # Bit 2: Accumulated Torque present (uint16 LE, 1/32 N·m)
            if (flags & 0x04) and offset + 1 < len(data):
                offset += 2

            # Bit 3: Accumulated Torque Source — informational flag only, no extra bytes

            # Bit 4: Wheel Revolution Data present (uint32 cumulative revs + uint16 last event time)
            if (flags & 0x10) and offset + 5 < len(data):
                offset += 6  # 4 bytes cumulative + 2 bytes event time

            # Bit 5: Crank Revolution Data present (uint16 cumulative cranks + uint16 last crank event time)
            # To calculate cadence you would need:
            #   RPM = (delta_cumulative_cranks / delta_time_seconds) * 60
            # where delta_time_seconds = delta_event_time / 1024  (units are 1/1024 s)
            # We skip it here but advance the offset past both fields.
            if (flags & 0x20) and offset + 3 < len(data):
                offset += 4   # 2 bytes cumulative + 2 bytes event time

            # Bit 6: Extreme Force Magnitudes present (uint16 max + uint16 min, N)
            if (flags & 0x40) and offset + 3 < len(data):
                offset += 4

            # Bit 7: Extreme Torque Magnitudes present (uint16 max + uint16 min, 1/32 N·m)
            if (flags & 0x80) and offset + 3 < len(data):
                offset += 4

            # Bits 8-11: Extreme Angles present (two uint12 values packed into 3 bytes)
            if (flags & 0x0F00) and offset + 2 < len(data):
                offset += 3

            # Bit 12: Top Dead Spot Angle present (uint16, degrees)
            if (flags & 0x1000) and offset + 1 < len(data):
                offset += 2

            # Bit 13: Bottom Dead Spot Angle present (uint16, degrees)
            if (flags & 0x2000) and offset + 1 < len(data):
                offset += 2

            # Bit 14: Accumulated Energy present (uint16, kJ)
            if (flags & 0x4000) and offset + 1 < len(data):
                offset += 2

            # Bit 15: Offset Compensation Indicator — informational flag, no extra bytes

            return result

        except struct.error as e:
            logging.warning(f"Cycling Power parsing error: {e}, data: {data.hex()}")
            return {}


class FTMSIndoorBikeParser:
    """Parses the FTMS Indoor Bike Data GATT characteristic (0x2AD2).

    The Fitness Machine Service is the modern Bluetooth profile for smart
    trainers.  A 16-bit flags field at the start of each notification
    tells the receiver exactly which optional fields are present, and in
    what order.  We parse only the fields we log (speed, cadence, power)
    and skip the rest by advancing ``offset``.

    Byte layout (Bluetooth Supplement §3.133):
      [0-1]  flags      (uint16 LE) — see bit descriptions below
      [2-3]  Inst. Speed (uint16 LE, 0.01 km/h) — always present per spec
      …      optional fields in spec-defined order follow
    """

    @staticmethod
    def parse(data: bytearray, debug: bool = False) -> Dict[str, Any]:
        """Parse an FTMS Indoor Bike Data notification.

        Returns a dict with any combination of:
          - ``speed_kph``   : float (km/h)
          - ``cadence_rpm`` : float (rpm)
          - ``power_w``     : int (watts, signed)
        """
        if len(data) < 2:
            logging.warning(f"FTMS data too short: {len(data)} bytes")
            return {}

        try:
            # Flags field (first 2 bytes, little-endian)
            flags = struct.unpack_from("<H", data, 0)[0]
            offset = 2

            if debug:
                logging.debug(f"FTMS flags: 0x{flags:04x}, raw: {data.hex()}")

            result = {}

            # Pre-decode every flag bit into a named boolean so the field-parsing
            # section below is easy to read and verify against the Bluetooth spec.
            # Bit 0: More Data flag — if SET, Instantaneous Speed is *not* included
            #        (our devices always include it, but we check just in case)
            has_avg_speed    = (flags & 0x02) != 0   # bit 1
            has_cadence      = (flags & 0x04) != 0   # bit 2  — Instantaneous Cadence
            has_avg_cadence  = (flags & 0x08) != 0   # bit 3
            has_distance     = (flags & 0x10) != 0   # bit 4  — Total Distance
            has_resistance   = (flags & 0x20) != 0   # bit 5  — Resistance Level
            has_power        = (flags & 0x40) != 0   # bit 6  — Instantaneous Power
            has_avg_power    = (flags & 0x80) != 0   # bit 7
            has_energy       = (flags & 0x100) != 0  # bit 8  — Expended Energy (3 sub-fields)
            has_hr           = (flags & 0x200) != 0  # bit 9  — Heart Rate
            has_met          = (flags & 0x400) != 0  # bit 10 — Metabolic Equivalent
            has_elapsed      = (flags & 0x800) != 0  # bit 11 — Elapsed Time
            has_remaining    = (flags & 0x1000) != 0 # bit 12 — Remaining Time

            # ── Parse fields in spec-mandated order ──────────────────────────

            # Instantaneous Speed — uint16 LE in units of 0.01 km/h
            # (always present unless "More Data" bit 0 is set)
            if offset + 1 < len(data):
                speed_raw = struct.unpack_from("<H", data, offset)[0]
                result["speed_kph"] = speed_raw * 0.01
                offset += 2

            # Average Speed — uint16 LE, 0.01 km/h (skip, not logged)
            if has_avg_speed and offset + 1 < len(data):
                offset += 2

            # Instantaneous Cadence — uint16 LE in units of 0.5 rpm
            if has_cadence and offset + 1 < len(data):
                cadence_raw = struct.unpack_from("<H", data, offset)[0]
                result["cadence_rpm"] = cadence_raw * 0.5
                offset += 2

            # Average Cadence — uint16 LE, 0.5 rpm (skip)
            if has_avg_cadence and offset + 1 < len(data):
                offset += 2

            # Total Distance — uint24 LE, metres (3 bytes, skip)
            if has_distance and offset + 2 < len(data):
                offset += 3

            # Resistance Level — sint16 LE (skip)
            if has_resistance and offset + 1 < len(data):
                offset += 2

            # Instantaneous Power — sint16 LE, watts (can be negative for regeneration)
            if has_power and offset + 1 < len(data):
                power_raw = struct.unpack_from("<h", data, offset)[0]   # signed
                result["power_w"] = power_raw
                offset += 2

            # Average Power — sint16 LE (skip)
            if has_avg_power and offset + 1 < len(data):
                offset += 2

            # Expended Energy — 3 sub-fields: Total uint16, Per-Hour uint16, Per-Minute uint8
            if has_energy and offset + 4 < len(data):
                offset += 5

            # Heart Rate — uint8, bpm (we get this from TICKR, skip)
            if has_hr and offset < len(data):
                offset += 1

            # Metabolic Equivalent — uint8, units of 0.1 MET (skip)
            if has_met and offset < len(data):
                offset += 1

            # Elapsed Time — uint16 LE, seconds (skip)
            if has_elapsed and offset + 1 < len(data):
                offset += 2

            # Remaining Time — uint16 LE, seconds (skip)
            if has_remaining and offset + 1 < len(data):
                offset += 2

            return result

        except struct.error as e:
            logging.warning(f"FTMS parsing error: {e}, data: {data.hex()}")
            return {}


class WahooDevice:
    """Manages the BLE connection lifecycle for a single Wahoo sensor.

    Each WahooDevice runs an ``asyncio`` task via ``run()``.  The task:
      1. Connects to the physical device
      2. Subscribes to GATT notifications on ``characteristic_uuid``
      3. Waits (sleeping 1 s at a time) until the connection drops
      4. Reconnects after ``RECONNECT_DELAY_SECONDS`` — indefinitely

    Parsed notification data is written to ``db_logger``.
    """

    def __init__(
        self,
        device: BLEDevice,
        characteristic_uuid: str,
        parser,
        db_logger: SQLiteLogger,
        debug: bool = False,
    ):
        self.device = device
        self.characteristic_uuid = characteristic_uuid
        self.parser = parser          # Static parser class with a .parse() method
        self.db_logger = db_logger
        self.debug = debug
        self.client: Optional[BleakClient] = None
        self.running = False          # Set False by stop() to exit the run loop cleanly

    async def notification_handler(self, sender: int, data: bytearray) -> None:
        """Handle incoming BLE notifications."""
        try:
            parsed = (
                self.parser.parse(data, debug=self.debug)
                if hasattr(self.parser.parse, "__code__")
                and "debug" in self.parser.parse.__code__.co_varnames
                else self.parser.parse(data)
            )

            if not parsed:
                return

            # Log to database based on device type
            if "bpm" in parsed:
                # Heart rate data
                hr_bpm = parsed["bpm"]
                rr_ms = (
                    parsed.get("rr_intervals_ms", [None])[0]
                    if parsed.get("rr_intervals_ms")
                    else None
                )

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
                    power_w=power, cadence_rpm=cadence, speed_kph=speed
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
            if self.debug or (self.device.name and "KICKR" in self.device.name):
                logging.info(
                    f"Available services and characteristics for {self.device.name}:"
                )
                for service in self.client.services:
                    logging.info(f"  Service: {service.uuid} - {service.description}")
                    for char in service.characteristics:
                        logging.info(
                            f"    Characteristic: {char.uuid} - {char.description} (Properties: {char.properties})"
                        )

            # Subscribe to notifications
            # Bleak's callback typing can vary by version; cast to a compatible
            # callable type for mypy while preserving runtime behavior.
            await self.client.start_notify(
                self.characteristic_uuid,
                cast(Callable[[Any, bytearray], Awaitable[None]], self.notification_handler),
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
        """Persistent connect → receive → reconnect loop.

        This coroutine runs for the entire lifetime of the program.
        It connects, waits while data flows in, and automatically
        reconnects if the device disconnects or the connection attempt fails.
        """
        self.running = True

        while self.running:
            try:
                connected = await self.connect_and_subscribe()

                if not connected:
                    logging.warning(
                        f"Will retry {self.device.name} in {RECONNECT_DELAY_SECONDS}s..."
                    )
                    await asyncio.sleep(RECONNECT_DELAY_SECONDS)
                    continue

                # Poll the connection state every second.
                # BLE notifications arrive on the asyncio event loop automatically
                # via the callback registered in connect_and_subscribe(); we just
                # need to keep the coroutine alive here.
                while self.running and self.client and self.client.is_connected:
                    await asyncio.sleep(1)

                # Reaching here means the device dropped the connection
                if self.running:
                    logging.warning(
                        f"{self.device.name} disconnected. Reconnecting in {RECONNECT_DELAY_SECONDS}s..."
                    )
                    await asyncio.sleep(RECONNECT_DELAY_SECONDS)

            except Exception as e:
                logging.error(f"Error in {self.device.name} main loop: {e}")
                if self.running:
                    await asyncio.sleep(RECONNECT_DELAY_SECONDS)

    async def stop(self) -> None:
        """Stop the device loop."""
        self.running = False
        await self.disconnect()


async def scan_for_device(
    device_name_contains: str,
    timeout: int = SCAN_TIMEOUT_SECONDS,
    show_all: bool = False,
) -> Optional[BLEDevice]:
    """Run a passive BLE scan and return the first device whose name
    contains ``device_name_contains`` (case-insensitive).

    Args:
        device_name_contains: Substring to look for in the device name.
        timeout: How many seconds to scan before giving up.
        show_all: If True, log every discovered device (useful for debugging).

    Returns:
        The matching BLEDevice, or None if nothing was found.
    """
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
    show_all_devices: bool = False,
) -> None:
    """Async entry point.

    1. Open the SQLite database
    2. Discover TICKR and KICKR (by address or by scanning)
    3. For KICKR: probe which GATT profile it exposes (FTMS or Cycling Power)
    4. Spin up one ``WahooDevice`` per sensor and run them with ``asyncio.gather``
    """

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
            debug=debug,
        )
        devices_list.append(tickr)
        tasks.append(asyncio.create_task(tickr.run()))

    if kickr_device:
        # Try to determine which characteristic to use
        kickr_char_uuid = INDOOR_BIKE_DATA_UUID
        kickr_parser: Type[Any] = FTMSIndoorBikeParser

        # Quick check to see if device has Cycling Power instead of FTMS
        try:
            async with BleakClient(kickr_device, timeout=5) as temp_client:
                service_uuids = [s.uuid.lower() for s in temp_client.services]
                has_ftms = FITNESS_MACHINE_SERVICE_UUID in service_uuids
                has_cycling_power = CYCLING_POWER_SERVICE_UUID in service_uuids

                if not has_ftms and has_cycling_power:
                    logging.info("KICKR using Cycling Power Service instead of FTMS")
                    kickr_char_uuid = CYCLING_POWER_MEASUREMENT_UUID
                    kickr_parser = CyclingPowerParser
        except Exception as e:
            logging.debug(f"Could not pre-check KICKR services: {e}")

        kickr = WahooDevice(
            device=kickr_device,
            characteristic_uuid=kickr_char_uuid,
            parser=kickr_parser,
            db_logger=db_logger,
            debug=debug,
        )
        devices_list.append(kickr)
        tasks.append(asyncio.create_task(kickr.run()))

    logging.info("Starting data collection. Press Ctrl+C to stop.")

    try:
        # Both device tasks run concurrently on the single asyncio event loop.
        # Ctrl-C raises KeyboardInterrupt which propagates as CancelledError here.
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logging.info("Shutting down...")
    finally:
        # Gracefully stop notifications and disconnect from every device
        for wd in devices_list:
            await wd.stop()


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Log live BLE data from Wahoo TICKR and KICKR devices to SQLite"
    )
    parser.add_argument(
        "--tickr-address",
        type=str,
        help="MAC address of TICKR device (optional, auto-scans if not provided)",
    )
    parser.add_argument(
        "--kickr-address",
        type=str,
        help="MAC address of KICKR device (optional, auto-scans if not provided)",
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging with raw BLE data"
    )
    parser.add_argument(
        "--show-all-devices",
        action="store_true",
        help="Show all discovered BLE devices during scan",
    )

    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Run async main
    try:
        asyncio.run(
            main_async(
                tickr_address=args.tickr_address,
                kickr_address=args.kickr_address,
                debug=args.debug,
                show_all_devices=args.show_all_devices,
            )
        )
    except KeyboardInterrupt:
        logging.info("Interrupted by user")


if __name__ == "__main__":
    main()
